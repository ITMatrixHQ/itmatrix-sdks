"""One shared, reconnecting protobuf WebSocket."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from google.protobuf.message import DecodeError
from typing_extensions import Self

from ._wire.ws_pb2 import Frame
from .errors import ITMError

_STOP = object()


@dataclass(frozen=True, slots=True)
class SubscribeOptions:
    symbol: str
    want: Sequence[str] | None = None
    timeframe: str | None = None
    expiry: str | None = None
    strike_gte: int | None = None
    strike_lte: int | None = None
    expiries: str | Sequence[str] | None = None
    by_expiry: bool | None = None
    component: str | None = None


class Subscription(AsyncIterator[Frame]):
    def __init__(
        self,
        stream: Stream,
        sub_id: str,
        topic: str,
        options: SubscribeOptions,
        maximum: int,
    ) -> None:
        self.id = sub_id
        self.topic = topic
        self.options = options
        self.effective: Any = None
        self._stream = stream
        self._queue: asyncio.Queue[Frame | BaseException | object] = asyncio.Queue(
            maxsize=maximum
        )
        self._started = False
        self._active = True

    def __await__(self) -> Any:
        return self.start().__await__()

    async def __aenter__(self) -> Self:
        return await self.start()

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> Frame:
        await self.start()
        value = await self._queue.get()
        if value is _STOP:
            raise StopAsyncIteration
        if isinstance(value, BaseException):
            raise value
        return value

    async def start(self) -> Self:
        if not self._started:
            self._started = True
            await self._stream._start(self)
        return self

    async def aclose(self) -> None:
        if not self._active:
            return
        self._active = False
        await self._stream._remove(self)
        self._finish()

    def _feed(self, frame: Frame) -> None:
        if not self._active or not _matches(self, frame):
            return
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            self._fail(
                ITMError(
                    status=0,
                    code="unknown",
                    message=f"stream consumer fell behind (queue limit {self._queue.maxsize})",
                )
            )
            asyncio.create_task(self._stream._remove(self))

    def _fail(self, error: BaseException) -> None:
        self._active = False
        _replace_queue(self._queue, error)

    def _finish(self) -> None:
        self._active = False
        _replace_queue(self._queue, _STOP)


class Stream:
    def __init__(
        self,
        *,
        url: str,
        ticket: Callable[[], Awaitable[Mapping[str, Any]]],
        connect: Any = None,
        max_queue: int = 64,
        min_reconnect: float = 0.25,
        max_reconnect: float = 10.0,
    ) -> None:
        if max_queue < 1:
            raise ValueError("max_queue must be positive")
        if min_reconnect < 0 or max_reconnect < min_reconnect:
            raise ValueError("reconnect delays must satisfy 0 <= min <= max")
        self._url = url
        self._ticket = ticket
        self._connect = connect
        self._max_queue = max_queue
        self._min_delay = min_reconnect
        self._max_delay = max_reconnect
        self._delay = min_reconnect
        self._socket: Any = None
        self._receiver: asyncio.Task[None] | None = None
        self._reconnector: asyncio.Task[None] | None = None
        self._subscriptions: dict[str, Subscription] = {}
        self._lock = asyncio.Lock()
        self._closed = False
        self._next_id = 1
        self._reconnect_after: float | None = None

    def subscribe(
        self,
        topic: str,
        *,
        symbol: str,
        want: Sequence[str] | None = None,
        timeframe: str | None = None,
        expiry: str | None = None,
        strike_gte: int | None = None,
        strike_lte: int | None = None,
        expiries: str | Sequence[str] | None = None,
        by_expiry: bool | None = None,
        component: str | None = None,
    ) -> Subscription:
        if topic not in {"stocks", "spot", "index", "chain", "gex"}:
            raise ValueError(f"unsupported stream topic {topic!r}")
        if not symbol:
            raise ValueError("symbol is required")
        sub_id = f"sdk-{self._next_id}"
        self._next_id += 1
        return Subscription(
            self,
            sub_id,
            topic,
            SubscribeOptions(
                symbol.upper(),
                want,
                timeframe,
                expiry,
                strike_gte,
                strike_lte,
                expiries,
                by_expiry,
                component,
            ),
            self._max_queue,
        )

    def stocks(
        self,
        symbol: str,
        want: Sequence[str] = ("trades", "mid", "bars", "quote"),
    ) -> Subscription:
        return self.subscribe("stocks", symbol=symbol, want=want)

    def spot(self, symbol: str) -> Subscription:
        return self.subscribe("spot", symbol=symbol)

    def gex(self, symbol: str, **options: Any) -> Subscription:
        return self.subscribe("gex", symbol=symbol, **options)

    def chain(self, symbol: str, **options: Any) -> Subscription:
        return self.subscribe("chain", symbol=symbol, **options)

    async def close(self) -> None:
        self._closed = True
        tasks = [task for task in (self._receiver, self._reconnector) if task]
        for task in tasks:
            task.cancel()
        for subscription in self._subscriptions.values():
            subscription._finish()
        self._subscriptions.clear()
        if self._socket is not None:
            await self._socket.close()
        self._socket = None
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _start(self, subscription: Subscription) -> None:
        self._closed = False
        async with self._lock:
            self._subscriptions[subscription.id] = subscription
            try:
                opened = await self._ensure_socket()
            except Exception:
                self._subscriptions.pop(subscription.id, None)
                raise
            if not opened:
                await self._send_subscription(subscription)

    async def _remove(self, subscription: Subscription) -> None:
        async with self._lock:
            if self._subscriptions.pop(subscription.id, None) is None:
                return
            if self._socket is not None:
                await self._socket.send(
                    json.dumps({"op": "unsub", "id": subscription.id})
                )
            if not self._subscriptions and self._socket is not None:
                await self._socket.close()
                self._socket = None

    async def _ensure_socket(self) -> bool:
        if self._socket is not None:
            return False
        ticket_data = await self._ticket()
        ticket = ticket_data.get("ticket")
        if not isinstance(ticket, str) or not ticket:
            raise ValueError("ticket response did not contain a ticket")
        connect = self._connect or _default_connect
        socket = await connect(self._url, max_queue=self._max_queue)
        await socket.send(
            json.dumps(
                {"op": "auth", "ticket": ticket, "proto": 3, "encoding": "protobuf"}
            )
        )
        while True:
            message = await socket.recv()
            if not isinstance(message, str):
                await socket.close()
                raise ITMError(
                    status=0,
                    code="unknown",
                    message="binary data arrived before stream authentication",
                )
            control = _control(message)
            if control.get("op") == "ping":
                await socket.send(json.dumps({"op": "pong", "t": control.get("t")}))
            elif control.get("op") == "authed":
                break
            elif control.get("op") in {"nack", "bye"}:
                await socket.close()
                raise ITMError(
                    status=0,
                    code=str(control.get("code", "unknown")),
                    message=str(
                        control.get("message", "stream authentication rejected")
                    ),
                )

        self._socket = socket
        self._delay = self._min_delay
        for subscription in self._subscriptions.values():
            await self._send_subscription(subscription)
        self._receiver = asyncio.create_task(self._receive(socket))
        return True

    async def _send_subscription(self, subscription: Subscription) -> None:
        if self._socket is None:
            return
        options = subscription.options
        payload = {
            "op": "sub",
            "id": subscription.id,
            "topic": subscription.topic,
            "symbol": options.symbol,
            "want": options.want,
            "timeframe": options.timeframe,
            "expiry": options.expiry,
            "strike_gte": options.strike_gte,
            "strike_lte": options.strike_lte,
            "expiries": options.expiries,
            "by_expiry": options.by_expiry,
            "component": options.component,
        }
        await self._socket.send(
            json.dumps(
                {key: value for key, value in payload.items() if value is not None}
            )
        )

    async def _receive(self, socket: Any) -> None:
        try:
            async for message in socket:
                if isinstance(message, str):
                    await self._handle_control(socket, _control(message))
                    continue
                try:
                    frame = Frame.FromString(bytes(message))
                except DecodeError as error:
                    failure = ITMError(
                        status=0,
                        code="unknown",
                        message="invalid protobuf websocket frame",
                        details={"cause": str(error)},
                    )
                    for subscription in self._subscriptions.values():
                        subscription._fail(failure)
                    self._subscriptions.clear()
                    return
                for subscription in self._subscriptions.values():
                    subscription._feed(frame)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001, S110
            # WebSocket libraries use their own connection-closed exception classes.
            pass
        finally:
            if socket is self._socket:
                self._socket = None
                if not self._closed and self._subscriptions:
                    self._schedule_reconnect()

    async def _handle_control(self, socket: Any, control: Mapping[str, Any]) -> None:
        op = control.get("op")
        if op == "ping":
            await socket.send(json.dumps({"op": "pong", "t": control.get("t")}))
        elif op == "ack":
            subscription = self._subscriptions.get(str(control.get("id", "")))
            if subscription:
                subscription.effective = control.get("effective")
        elif op == "nack":
            subscription = self._subscriptions.pop(str(control.get("id", "")), None)
            if subscription:
                subscription._fail(
                    ITMError(
                        status=0,
                        code=str(control.get("code", "unknown")),
                        message=str(
                            control.get("message", "stream subscription rejected")
                        ),
                    )
                )
        elif op == "bye":
            value = control.get("reconnect_after_ms")
            if isinstance(value, int | float) and value >= 0:
                self._reconnect_after = value / 1000
            await socket.close()

    def _schedule_reconnect(self) -> None:
        if self._reconnector is not None and not self._reconnector.done():
            return
        self._reconnector = asyncio.create_task(self._reconnect())

    async def _reconnect(self) -> None:
        wait = (
            self._reconnect_after if self._reconnect_after is not None else self._delay
        )
        self._reconnect_after = None
        await asyncio.sleep(wait)
        try:
            async with self._lock:
                if not self._closed and self._subscriptions:
                    await self._ensure_socket()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            self._delay = min(max(self._delay * 2, self._min_delay), self._max_delay)
            self._reconnector = None
            self._schedule_reconnect()


async def _default_connect(url: str, **options: Any) -> Any:
    try:
        from websockets.asyncio.client import connect
    except ModuleNotFoundError as error:
        raise ImportError(
            'Streaming requires the optional dependency: pip install "itmatrix[stream]"'
        ) from error

    return await connect(url, **options)


def _control(message: str) -> Mapping[str, Any]:
    try:
        value = json.loads(message)
    except json.JSONDecodeError as error:
        raise ITMError(
            status=0, code="unknown", message="invalid websocket control frame"
        ) from error
    if not isinstance(value, dict):
        raise ITMError(
            status=0, code="unknown", message="invalid websocket control frame"
        )
    return value


def _matches(subscription: Subscription, frame: Frame) -> bool:
    if frame.symbol != subscription.options.symbol:
        return False
    fields = {
        "stocks": ("trade", "mid", "bar_open", "bar_close", "quote"),
        "spot": ("spot",),
        "index": ("index_value",),
        "gex": ("gex_snap", "gex_delta"),
        "chain": ("chain_snap", "chain_delta"),
    }[subscription.topic]
    return any(frame.HasField(field) for field in fields)


def _replace_queue(
    queue: asyncio.Queue[Frame | BaseException | object],
    value: Frame | BaseException | object,
) -> None:
    while not queue.empty():
        queue.get_nowait()
    queue.put_nowait(value)
