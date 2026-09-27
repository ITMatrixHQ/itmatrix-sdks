import asyncio
import json
import unittest
from typing import Any

from itmatrix._core import Stream
from itmatrix._core._wire.ws_pb2 import Frame

_CLOSED = object()


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self._messages: asyncio.Queue[str | bytes | object] = asyncio.Queue()
        self._closed = False

    async def recv(self) -> str:
        return json.dumps({"op": "authed"})

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    def __aiter__(self) -> "FakeSocket":
        return self

    async def __anext__(self) -> str | bytes:
        message = await self._messages.get()
        if message is _CLOSED:
            raise StopAsyncIteration
        return message  # type: ignore[return-value]

    async def emit(self, message: str | bytes) -> None:
        await self._messages.put(message)

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self._messages.put(_CLOSED)


async def wait_for(predicate: Any) -> None:
    for _attempt in range(100):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition was not reached")


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_socket_routes_multiple_subscriptions(self) -> None:
        tickets = 0
        sockets: list[FakeSocket] = []

        async def ticket() -> dict[str, Any]:
            nonlocal tickets
            tickets += 1
            return {"ticket": f"ticket-{tickets}", "exp": 1}

        async def connect(_url: str, **_options: Any) -> FakeSocket:
            socket = FakeSocket()
            sockets.append(socket)
            return socket

        stream = Stream(
            url="wss://stream.test/v2/ws",
            ticket=ticket,
            connect=connect,
            min_reconnect=0,
            max_reconnect=0,
        )
        stocks = stream.stocks("spy")
        spot = stream.spot("spy")
        await stocks.start()
        await spot.start()

        stock_frame = Frame(symbol="SPY")
        stock_frame.trade.ts_ms = 7
        stock_frame.trade.price_scaled = 6_000_000
        stock_frame.trade.size = 2
        spot_frame = Frame(symbol="SPY")
        spot_frame.spot.ts_ms = 8
        spot_frame.spot.price_scaled = 6_000_100

        stock_result = asyncio.create_task(anext(stocks))
        await sockets[0].emit(stock_frame.SerializeToString())
        spot_result = asyncio.create_task(anext(spot))
        await sockets[0].emit(spot_frame.SerializeToString())
        await sockets[0].emit(
            json.dumps({"op": "ack", "id": stocks.id, "effective": {"symbol": "SPY"}})
        )
        await sockets[0].emit(json.dumps({"op": "ping", "t": 42}))

        self.assertEqual((await asyncio.wait_for(stock_result, 1)).trade.ts_ms, 7)
        self.assertEqual((await asyncio.wait_for(spot_result, 1)).spot.ts_ms, 8)
        await wait_for(lambda: stocks.effective is not None)
        self.assertEqual(stocks.effective, {"symbol": "SPY"})
        self.assertEqual(tickets, 1)
        self.assertEqual(len(sockets), 1)
        self.assertEqual(
            sockets[0].sent[0],
            {
                "op": "auth",
                "ticket": "ticket-1",
                "proto": 3,
                "encoding": "protobuf",
            },
        )
        await wait_for(lambda: {"op": "pong", "t": 42} in sockets[0].sent)

        await stocks.aclose()
        await spot.aclose()
        await stream.close()

    async def test_disconnect_mints_new_ticket_and_resubscribes(self) -> None:
        tickets = 0
        sockets: list[FakeSocket] = []

        async def ticket() -> dict[str, Any]:
            nonlocal tickets
            tickets += 1
            return {"ticket": f"ticket-{tickets}", "exp": 1}

        async def connect(_url: str, **_options: Any) -> FakeSocket:
            socket = FakeSocket()
            sockets.append(socket)
            return socket

        stream = Stream(
            url="wss://stream.test/v2/ws",
            ticket=ticket,
            connect=connect,
            min_reconnect=0,
            max_reconnect=0,
        )
        stocks = await stream.stocks("SPY")
        await sockets[0].close()
        await wait_for(lambda: len(sockets) == 2)
        await wait_for(
            lambda: any(message.get("op") == "sub" for message in sockets[1].sent)
        )

        frame = Frame(symbol="SPY")
        frame.trade.ts_ms = 9
        await sockets[1].emit(frame.SerializeToString())

        self.assertEqual((await asyncio.wait_for(anext(stocks), 1)).trade.ts_ms, 9)
        self.assertEqual(tickets, 2)
        await stocks.aclose()
        await stream.close()


if __name__ == "__main__":
    unittest.main()


class OptionalDependencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_websockets_explains_extra(self):
        from unittest.mock import patch

        from itmatrix._core.stream import _default_connect

        with (
            patch.dict("sys.modules", {"websockets.asyncio.client": None}),
            self.assertRaisesRegex(ImportError, r"itmatrix\[stream\]"),
        ):
            await _default_connect("wss://example.invalid")
