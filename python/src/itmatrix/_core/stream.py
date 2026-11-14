"""One shared, reconnecting protobuf WebSocket.

An ``itm_`` API key connects with protocol 8, the ``itmatrixhq.v1`` subprotocol
and a declared client name, and may subscribe to ``gex`` books. Any other
credential keeps the 1.0.0 protocol.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from google.protobuf.message import DecodeError
from typing_extensions import Self, TypeVar

from ._wire.ws_pb2 import Frame
from .errors import ITMError
from .gex_book import GexBookFilter, GexBookState, GexBookTracker
from .version import client_name

_STOP = object()

CLIENT_SUBPROTOCOL = "itmatrixhq.v1"
KEYED_PROTO = 8

# Refusals that retrying cannot fix. The SDK raises and stops.
_TERMINAL_REASONS = frozenset(
    {
        "stream_preview",
        "client_outdated",
        "client_required",
        "proto_required",
        "protobuf_required",
    }
)
# Close and refusal codes at authentication that retrying cannot fix.
_TERMINAL_AUTH_CODES = frozenset({"not_entitled_tier", "invalid_params"})

HEARTBEAT_INTERVAL = 20.0  # seconds between our own pings
DEAD_AFTER = 40.0  # seconds of silence before the connection is treated as dead
CONNECTION_LIMIT_BACKOFF = 30.0  # minimum wait after a connection_limit refusal
RESYNC_INTERVAL = 5.5  # the server honours one resync per subscription per 5 s
AUTH_TIMEOUT = 20.0
MAX_RETRY_AFTER = 300.0


class StreamError(ITMError):
    """A stream refusal or failure.

    ``reason`` is the server's refusal reason (for example ``stream_preview`` or
    ``connection_limit``) when it sent one. ``terminal`` is true when the SDK
    will not retry. ``retry_after_seconds`` carries the server's wait hint.
    """

    def __init__(
        self,
        *,
        code: str,
        message: str,
        reason: str | None = None,
        terminal: bool = False,
        status: int = 0,
        details: Mapping[str, Any] | None = None,
        request_id: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(
            status=status,
            code=code,
            message=message,
            details=details,
            request_id=request_id,
            retry_after_seconds=retry_after_seconds,
        )
        self.reason = reason
        self.terminal = terminal


T = TypeVar("T", default=Frame)


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


class Subscription(AsyncIterator[T]):
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
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=maximum)
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

    async def __anext__(self) -> T:
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
        self._put(frame)

    def _on_connect(self) -> None:
        """Called each time the connection is (re)established."""

    def _put(self, value: Any) -> None:
        try:
            self._queue.put_nowait(value)
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


class GexBookSubscription(Subscription[GexBookState]):
    """A reconstructed GEX book.

    Yields a ``GexBookState`` after every applied frame, passed through the
    subscription's filter when it has one. :meth:`set_filter` swaps the filter
    on the live subscription without resubscribing.
    """

    def __init__(
        self,
        stream: Stream,
        sub_id: str,
        topic: str,
        options: SubscribeOptions,
        maximum: int,
        filter: GexBookFilter | None = None,
    ) -> None:
        super().__init__(stream, sub_id, topic, options, maximum)
        self._book = GexBookTracker(options.symbol)
        self._filter = _check_filter(filter)

    @property
    def filter(self) -> GexBookFilter | None:
        """The filter applied to every yielded state, or ``None`` for the whole book."""
        return self._filter

    def set_filter(self, filter: GexBookFilter | None) -> None:
        """Filter every state yielded from now on; ``None`` yields the whole book.

        The server subscription is unchanged. States already waiting in the
        queue keep the filter they were yielded with; :attr:`view` gives the
        current book under the new filter straight away.
        """
        self._filter = _check_filter(filter)

    @property
    def state(self) -> GexBookState | None:
        """The latest whole book, unfiltered, or ``None`` before the first keyframe."""
        return self._book.state

    @property
    def view(self) -> GexBookState | None:
        """The latest book under the current filter, or ``None`` before a keyframe."""
        state = self._book.state
        if state is None or self._filter is None:
            return state
        return self._filter(state)

    def _feed(self, frame: Frame) -> None:
        if not self._active or frame.symbol != self.options.symbol:
            return
        state, resync = self._book.apply(frame)
        if resync:
            self._stream._request_resync(self)
        if state is None:
            return
        if self._filter is not None:
            try:
                state = self._filter(state)
            except Exception as error:  # noqa: BLE001
                # A caller's filter failed: end this subscription with its error.
                self._fail(error)
                asyncio.create_task(self._stream._remove(self))
                return
        self._put(state)

    def _on_connect(self) -> None:
        # A new connection starts with a keyframe. Nothing before it applies.
        self._book.reset()


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
        key_auth: bool | Callable[[], bool | Awaitable[bool]] = False,
        client: str | None = None,
        ping_interval: float = HEARTBEAT_INTERVAL,
        dead_after: float = DEAD_AFTER,
        connection_limit_backoff: float = CONNECTION_LIMIT_BACKOFF,
        resync_interval: float = RESYNC_INTERVAL,
        auth_timeout: float = AUTH_TIMEOUT,
        rng: Callable[[], float] = random.random,
    ) -> None:
        if max_queue < 1:
            raise ValueError("max_queue must be positive")
        if min_reconnect < 0 or max_reconnect < min_reconnect:
            raise ValueError("reconnect delays must satisfy 0 <= min <= max")
        if ping_interval <= 0 or dead_after <= 0:
            raise ValueError("ping_interval and dead_after must be positive")
        self._url = url
        self._ticket = ticket
        self._connect = connect
        self._max_queue = max_queue
        self._min_delay = min_reconnect
        self._max_delay = max_reconnect
        # Whether the credential is an API key. Keys use protocol 8 and may
        # subscribe to books; every other credential keeps protocol 3.
        self._key_auth = key_auth
        self._client = client or _client_name()
        self._ping_interval = ping_interval
        self._dead_after = dead_after
        self._limit_backoff = connection_limit_backoff
        self._resync_interval = resync_interval
        self._auth_timeout = auth_timeout
        self._rng = rng
        self._socket: Any = None
        self._receiver: asyncio.Task[None] | None = None
        self._reconnector: asyncio.Task[None] | None = None
        self._heartbeat: asyncio.Task[None] | None = None
        self._background: set[asyncio.Task[None]] = set()
        self._subscriptions: dict[str, Subscription[Any]] = {}
        self._lock = asyncio.Lock()
        self._closed = False
        self._next_id = 1
        self._reconnect_after: float | None = None
        self._last_rx = time.monotonic()
        self._resync_sent: dict[str, float] = {}

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
        return Subscription(
            self,
            self._new_id(),
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

    def _new_id(self) -> str:
        sub_id = f"sdk-{self._next_id}"
        self._next_id += 1
        return sub_id

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

    def gex_book(
        self, symbol: str, filter: GexBookFilter | None = None
    ) -> GexBookSubscription:
        """Stream a live GEX book for one underlying. Requires an ``itm_`` API key.

        ``async with stream.gex_book("SPY") as book`` then ``async for state in
        book`` yields a :class:`GexBookState` after every applied keyframe or
        update. The SDK keeps the book correct: it checks versions and the CRC,
        asks for a fresh keyframe when they disagree, and reconnects with the
        backoff the service asks for. Terminal refusals raise
        :class:`StreamError` from the iterator.

        ``filter`` (see :mod:`itmatrix.filters`, for example ``nearest(20)``)
        narrows every yielded state to a subset of the book's strikes;
        ``book.set_filter(...)`` swaps it on the live subscription.
        """
        if not symbol:
            raise ValueError("symbol is required")
        return GexBookSubscription(
            self,
            self._new_id(),
            "gex",
            SubscribeOptions(symbol.upper()),
            self._max_queue,
            filter,
        )

    def chain(self, symbol: str, **options: Any) -> Subscription:
        return self.subscribe("chain", symbol=symbol, **options)

    async def close(self) -> None:
        self._closed = True
        tasks = [
            task
            for task in (
                self._receiver,
                self._reconnector,
                self._heartbeat,
                *self._background,
            )
            if task
        ]
        for task in tasks:
            task.cancel()
        for subscription in self._subscriptions.values():
            subscription._finish()
        self._subscriptions.clear()
        self._resync_sent.clear()
        if self._socket is not None:
            await self._socket.close()
        self._socket = None
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _uses_key(self) -> bool:
        value = self._key_auth
        if callable(value):
            value = value()
        if inspect.isawaitable(value):
            value = await value
        return bool(value)

    async def _start(self, subscription: Subscription[Any]) -> None:
        self._closed = False
        if isinstance(subscription, GexBookSubscription):
            if not await self._uses_key():
                raise StreamError(
                    code="invalid_params",
                    reason="api_key_required",
                    terminal=True,
                    message="gex_book needs an itm_ API key; token sessions use stream.gex()",
                )
            async with self._lock:
                self._subscriptions[subscription.id] = subscription
                if self._socket is not None:
                    await self._send_subscription(subscription)
                else:
                    # Connect in the background: refusals and outages reach the
                    # iterator, and the SDK keeps retrying what is retryable.
                    self._schedule_reconnect(immediate=True)
            return
        async with self._lock:
            self._subscriptions[subscription.id] = subscription
            try:
                opened = await self._ensure_socket()
            except Exception:
                self._subscriptions.pop(subscription.id, None)
                raise
            if not opened:
                await self._send_subscription(subscription)

    async def _remove(self, subscription: Subscription[Any]) -> None:
        async with self._lock:
            if self._subscriptions.pop(subscription.id, None) is None:
                return
            self._resync_sent.pop(subscription.id, None)
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
        keyed = await self._uses_key()
        ticket_data = await self._ticket()
        ticket = ticket_data.get("ticket")
        if not isinstance(ticket, str) or not ticket:
            raise ValueError("ticket response did not contain a ticket")
        connect = self._connect or _default_connect
        options: dict[str, Any] = {"max_queue": self._max_queue}
        auth: dict[str, Any] = {
            "op": "auth",
            "ticket": ticket,
            "proto": 3,
            "encoding": "protobuf",
        }
        if keyed:
            # The SDK's own heartbeat is the liveness rule, and keyed books are
            # sent uncompressed, so neither socket-level feature is wanted.
            options.update(
                subprotocols=[CLIENT_SUBPROTOCOL], compression=None, ping_interval=None
            )
            auth.update(proto=KEYED_PROTO, client=self._client)
        socket = await connect(self._url, **options)
        try:
            await socket.send(json.dumps(auth))
            self._last_rx = time.monotonic()
            await self._await_authed(socket)
        except BaseException:
            with contextlib.suppress(Exception):
                await socket.close()
            raise

        self._socket = socket
        self._last_rx = time.monotonic()
        for subscription in self._subscriptions.values():
            subscription._on_connect()
            await self._send_subscription(subscription)
        self._receiver = asyncio.create_task(self._receive(socket))
        self._heartbeat = asyncio.create_task(self._beat(socket))
        return True

    async def _await_authed(self, socket: Any) -> None:
        while True:
            message = await asyncio.wait_for(socket.recv(), self._auth_timeout)
            if not isinstance(message, str):
                raise ITMError(
                    status=0,
                    code="unknown",
                    message="binary data arrived before stream authentication",
                )
            control = _control(message)
            op = control.get("op")
            if op == "ping":
                await socket.send(json.dumps(_pong(control)))
            elif op == "authed":
                return
            elif op == "nack":
                raise _refusal(control, "stream authentication rejected", auth=True)
            elif op == "bye":
                raise _goodbye(control)

    async def _send_subscription(self, subscription: Subscription[Any]) -> None:
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
                self._last_rx = time.monotonic()
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
                for subscription in list(self._subscriptions.values()):
                    subscription._feed(frame)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001, S110
            # WebSocket libraries use their own connection-closed exception classes.
            pass
        finally:
            if socket is self._socket:
                self._socket = None
                if self._heartbeat is not None:
                    self._heartbeat.cancel()
                if not self._closed and self._subscriptions:
                    self._schedule_reconnect()

    async def _beat(self, socket: Any) -> None:
        """Ping every interval; close a connection that has been silent too long."""
        next_ping = time.monotonic() + self._ping_interval
        try:
            while True:
                dead_at = self._last_rx + self._dead_after
                await asyncio.sleep(max(min(next_ping, dead_at) - time.monotonic(), 0))
                now = time.monotonic()
                if now >= self._last_rx + self._dead_after:
                    await socket.close()
                    return
                if now >= next_ping:
                    await socket.send(json.dumps({"op": "ping"}))
                    next_ping = now + self._ping_interval
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            # The receiver sees the closed socket and owns the reconnect.
            return

    async def _handle_control(self, socket: Any, control: Mapping[str, Any]) -> None:
        op = control.get("op")
        if op == "ping":
            await socket.send(json.dumps(_pong(control)))
        elif op == "ack":
            subscription = self._subscriptions.get(str(control.get("id", "")))
            if subscription:
                subscription.effective = control.get("effective")
        elif op == "nack":
            subscription = self._subscriptions.pop(str(control.get("id", "")), None)
            if subscription:
                subscription._fail(
                    _refusal(control, "stream subscription rejected", auth=False)
                )
        elif op == "bye":
            self._reconnect_after = _goodbye(control).retry_after_seconds
            await socket.close()

    def _request_resync(self, subscription: Subscription[Any]) -> None:
        """Ask for a fresh keyframe, at most once per interval per subscription."""
        now = time.monotonic()
        last = self._resync_sent.get(subscription.id)
        if last is not None and now - last < self._resync_interval:
            return
        self._resync_sent[subscription.id] = now
        task = asyncio.create_task(self._send_resync(subscription.id))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _send_resync(self, sub_id: str) -> None:
        socket = self._socket
        if socket is None or sub_id not in self._subscriptions:
            return
        try:
            await socket.send(json.dumps({"op": "resync", "id": sub_id}))
        except Exception:  # noqa: BLE001
            # A dead socket reconnects, and the reconnect brings a keyframe.
            return

    def _schedule_reconnect(self, immediate: bool = False) -> None:
        if self._reconnector is not None and not self._reconnector.done():
            return
        self._reconnector = asyncio.create_task(self._reconnect(immediate))

    async def _reconnect(self, immediate: bool) -> None:
        # The first attempt of an immediate connect is not a retry.
        failures = -1 if immediate else 0
        explicit: float | None = 0.0 if immediate else None
        while True:
            hint, self._reconnect_after = self._reconnect_after, None
            if hint is not None:
                wait = hint
            elif explicit is not None:
                wait = explicit
            else:
                cap = min(self._max_delay, self._min_delay * 2 ** max(failures, 0))
                wait = self._rng() * cap  # full jitter
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                async with self._lock:
                    if self._closed or not self._subscriptions:
                        return
                    await self._ensure_socket()
                return
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001
                terminal, explicit = self._plan(error)
                if terminal:
                    self._fail_all(error)
                    return
                failures += 1

    def _plan(self, error: Exception) -> tuple[bool, float | None]:
        """Decide what a failed connect means: ``(terminal, wait)``.

        ``wait`` is the delay the server asked for, or ``None`` for the
        exponential backoff.
        """
        if isinstance(error, ImportError):
            return True, None
        if not isinstance(error, ITMError):
            return False, None  # network, timeout, malformed handshake
        if isinstance(error, StreamError) and error.terminal:
            return True, None
        reason = _reason_of(error)
        if reason in _TERMINAL_REASONS:
            return True, None
        if reason == "connection_limit":
            return False, self._limit_backoff * (1 + 0.5 * self._rng())
        hint = error.retry_after_seconds
        if hint is not None and hint >= 0:
            return False, min(hint, MAX_RETRY_AFTER)
        if isinstance(error, StreamError) or error.status in {0, 408, 429}:
            return False, None
        # Other 4xx answers (bad key, revoked, forbidden) will not change on retry.
        return 400 <= error.status < 500, None

    def _fail_all(self, error: Exception) -> None:
        failure: Exception = error
        if isinstance(error, ITMError) and not isinstance(error, StreamError):
            failure = StreamError(
                code=error.code,
                message=error.message,
                reason=_reason_of(error),
                terminal=True,
                status=error.status,
                details=error.details,
                request_id=error.request_id,
                retry_after_seconds=error.retry_after_seconds,
            )
        for subscription in self._subscriptions.values():
            subscription._fail(failure)
        self._subscriptions.clear()
        self._resync_sent.clear()


def _check_filter(filter: GexBookFilter | None) -> GexBookFilter | None:
    if filter is not None and not callable(filter):
        raise TypeError(
            "filter must be a function of a GexBookState, such as itmatrix.filters.nearest(20)"
        )
    return filter


def _client_name() -> str:
    return client_name()


def _reason_of(error: ITMError) -> str | None:
    reason = getattr(error, "reason", None)
    if reason is None and error.details is not None:
        reason = error.details.get("reason")
    return reason if isinstance(reason, str) else None


def _pong(control: Mapping[str, Any]) -> dict[str, Any]:
    pong: dict[str, Any] = {"op": "pong"}
    if "t" in control:
        pong["t"] = control["t"]
    return pong


def _refusal(control: Mapping[str, Any], default: str, *, auth: bool) -> StreamError:
    """A ``nack`` as an error. At authentication, some refusals are final."""
    code = str(control.get("code", "unknown"))
    reason = control.get("reason")
    reason = reason if isinstance(reason, str) else None
    details = {"limit": control["limit"]} if "limit" in control else None
    return StreamError(
        code=code,
        message=str(control.get("message") or reason or default),
        reason=reason,
        terminal=not auth
        or reason in _TERMINAL_REASONS
        or code in _TERMINAL_AUTH_CODES,
        details=details,
    )


def _goodbye(control: Mapping[str, Any]) -> StreamError:
    """A ``bye`` as an error carrying how long to wait before reconnecting."""
    code = str(control.get("code", control.get("reason", "unknown")))
    hint = control.get("reconnect_after_ms")
    if isinstance(hint, int | float) and not isinstance(hint, bool) and hint >= 0:
        wait: float | None = hint / 1000
    elif code == "reauth":
        wait = 0.0  # the ticket is the thing that expired: mint a new one now
    else:
        wait = None
    reason = control.get("reason")
    return StreamError(
        code=code,
        message=f"stream closed by server ({code})",
        reason=reason if isinstance(reason, str) else code,
        retry_after_seconds=wait,
    )


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
