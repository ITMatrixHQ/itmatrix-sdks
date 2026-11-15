import asyncio
import json
import time
import unittest
from pathlib import Path
from typing import Any

import httpx

import itmatrix as itm
from itmatrix._core import Stream
from itmatrix._core._wire.ws_pb2 import Frame
from itmatrix._core.errors import ITMError
from itmatrix._core.stream import StreamError

_CLOSED = object()


class FakeSocket:
    def __init__(self, replies: list[dict[str, Any]] | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self._messages: asyncio.Queue[str | bytes | object] = asyncio.Queue()
        self._closed = False
        # What the server answers to the auth frame. Default: authed.
        self._replies = list(replies) if replies else [{"op": "authed"}]

    async def recv(self) -> str:
        return json.dumps(self._replies.pop(0) if self._replies else {"op": "authed"})

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

    @property
    def closed(self) -> bool:
        return self._closed


async def wait_for(predicate: Any) -> None:
    for _attempt in range(100):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition was not reached")


class OptionalDependencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_websockets_explains_extra(self):
        from unittest.mock import patch

        from itmatrix._core.stream import _default_connect

        with (
            patch.dict("sys.modules", {"websockets.asyncio.client": None}),
            self.assertRaisesRegex(ImportError, r"itmatrix\[stream\]"),
        ):
            await _default_connect("wss://example.invalid")


# ---------------------------------------------------------------------------
# Keyed GEX books. The frames are the golden wire vectors in
# fixtures/gex_strike_book_vectors.json (see test_gex_book.py): a keyframe at
# version 7, then updates to versions 8 and 9.
# ---------------------------------------------------------------------------

_VECTORS = {
    step["name"]: step
    for step in json.loads(
        (
            Path(__file__).parent / "fixtures" / "gex_strike_book_vectors.json"
        ).read_text()
    )["steps"]
}
KEYFRAME_HEX = _VECTORS["keyframe"]["frame_hex"]
UPDATE_HEX = _VECTORS["update_1"]["frame_hex"]
UPDATE_2_HEX = _VECTORS["update_2"]["frame_hex"]
CLIENT = "itmatrix-python/1.2.1"


def raw(hex_value: str) -> bytes:
    return bytes.fromhex(hex_value)


def bad_crc_update() -> bytes:
    frame = Frame.FromString(raw(UPDATE_HEX))
    frame.gex_strike_book_update.crc ^= 1
    return frame.SerializeToString()


def keyframe_version(version: int) -> bytes:
    frame = Frame.FromString(raw(KEYFRAME_HEX))
    frame.gex_strike_book.version = version
    return frame.SerializeToString()


def update_version(version: int) -> bytes:
    frame = Frame.FromString(raw(UPDATE_HEX))
    frame.gex_strike_book_update.version = version
    return frame.SerializeToString()


async def wait_until(predicate: Any, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition was not reached")
        await asyncio.sleep(0.005)


def subs(socket: FakeSocket) -> list[dict[str, Any]]:
    return [m for m in socket.sent if m.get("op") == "sub"]


class Harness:
    """A scripted server behind Stream: one plan per connect, one entry per mint."""

    def __init__(
        self,
        plans: list[Any] | None = None,
        mints: list[Exception | None] | None = None,
        **options: Any,
    ) -> None:
        self.plans = list(plans or [])
        self.mints = list(mints or [])
        self.sockets: list[FakeSocket] = []
        self.options: list[dict[str, Any]] = []
        self.connected_at: list[float] = []
        self.tickets = 0
        options.setdefault("min_reconnect", 0)
        options.setdefault("max_reconnect", 0)
        self.stream = Stream(
            url="wss://stream.test/v2/ws",
            ticket=self._ticket,
            connect=self._connect,
            key_auth=True,
            client=CLIENT,
            **options,
        )

    async def _ticket(self) -> dict[str, Any]:
        self.tickets += 1
        if self.mints:
            failure = self.mints.pop(0)
            if failure is not None:
                raise failure
        return {"ticket": f"ticket-{self.tickets}", "exp": 1}

    async def _connect(self, _url: str, **options: Any) -> FakeSocket:
        plan = self.plans.pop(0) if self.plans else None
        if isinstance(plan, Exception):
            raise plan
        socket = FakeSocket(plan)
        self.sockets.append(socket)
        self.options.append(options)
        self.connected_at.append(time.monotonic())
        return socket

    async def ready(self, count: int = 1) -> FakeSocket:
        """Wait until the count-th socket is authenticated and subscribed."""
        await wait_until(
            lambda: len(self.sockets) >= count and subs(self.sockets[count - 1])
        )
        return self.sockets[count - 1]


class KeyedStreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        for harness in getattr(self, "harnesses", []):
            await harness.stream.close()

    def harness(self, *args: Any, **kwargs: Any) -> Harness:
        harness = Harness(*args, **kwargs)
        self.harnesses = [*getattr(self, "harnesses", []), harness]
        return harness

    async def test_auth_subprotocol_subscribe_and_book_updates(self) -> None:
        h = self.harness()
        book = h.stream._book_feed("spy")
        await book.start()
        socket = await h.ready()

        self.assertEqual(h.options[0]["subprotocols"], ["itmatrixhq.v1"])
        self.assertIsNone(h.options[0]["compression"])
        self.assertEqual(
            socket.sent[0],
            {
                "op": "auth",
                "ticket": "ticket-1",
                "proto": 8,
                "encoding": "protobuf",
                "client": CLIENT,
            },
        )
        self.assertEqual(
            subs(socket),
            [{"op": "sub", "id": "sdk-1", "topic": "gex", "symbol": "SPY"}],
        )

        await socket.emit(raw(KEYFRAME_HEX))
        first = await asyncio.wait_for(anext(book), 1)
        self.assertEqual(
            (first.symbol, first.version, first.reason), ("SPY", 7, "subscribe")
        )
        self.assertEqual(first.strikes, (595.0, 600.0, 602.5, 605.0, 610.0))
        await socket.emit(raw(UPDATE_HEX))
        second = await asyncio.wait_for(anext(book), 1)
        self.assertEqual(
            (second.version, second.changed, second.spot), (8, (1, 4), 601.5)
        )
        self.assertEqual(second.gex[4], 28_125_000.0)
        await socket.emit(raw(UPDATE_2_HEX))
        third = await asyncio.wait_for(anext(book), 1)
        self.assertEqual(
            (third.version, third.changed, third.flip_point), (9, (0, 2), None)
        )
        self.assertNotIn({"op": "resync", "id": "sdk-1"}, socket.sent)

    async def test_token_credentials_cannot_open_a_book(self) -> None:
        tickets = 0

        async def ticket() -> dict[str, Any]:
            nonlocal tickets
            tickets += 1
            return {"ticket": "t"}

        stream = Stream(url="wss://stream.test/v2/ws", ticket=ticket)
        with self.assertRaises(StreamError) as caught:
            await stream._book_feed("SPY").start()
        self.assertEqual(caught.exception.reason, "api_key_required")
        self.assertEqual(tickets, 0)

    async def test_crc_failure_resyncs_once_and_drops_updates_until_a_keyframe(
        self,
    ) -> None:
        h = self.harness(resync_interval=60)
        book = h.stream._book_feed("SPY")
        await book.start()
        socket = await h.ready()
        await socket.emit(raw(KEYFRAME_HEX))
        await asyncio.wait_for(anext(book), 1)

        await socket.emit(bad_crc_update())
        await wait_until(lambda: {"op": "resync", "id": "sdk-1"} in socket.sent)
        # A good update now would be in order, but it is dropped without a second resync.
        await socket.emit(raw(UPDATE_HEX))
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(anext(book), 0.05)
        self.assertEqual(socket.sent.count({"op": "resync", "id": "sdk-1"}), 1)

        await socket.emit(keyframe_version(8))
        recovered = await asyncio.wait_for(anext(book), 1)
        self.assertEqual(recovered.version, 8)
        # The book applies updates again after the keyframe. Same strikes, same
        # result as the vector update, one version later: the same CRC.
        await socket.emit(update_version(9))
        self.assertEqual((await asyncio.wait_for(anext(book), 1)).version, 9)

    async def test_corrupted_keyframe_resyncs_and_the_next_keyframe_recovers(
        self,
    ) -> None:
        h = self.harness(resync_interval=60)
        book = h.stream._book_feed("SPY")
        await book.start()
        socket = await h.ready()
        bad = Frame.FromString(raw(KEYFRAME_HEX))
        bad.gex_strike_book.crc ^= 1
        await socket.emit(bad.SerializeToString())
        await wait_until(lambda: {"op": "resync", "id": "sdk-1"} in socket.sent)
        await socket.emit(raw(UPDATE_HEX))
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(anext(book), 0.05)
        await socket.emit(raw(KEYFRAME_HEX))
        self.assertEqual((await asyncio.wait_for(anext(book), 1)).version, 7)

    async def test_resync_repeats_after_the_interval_while_updates_are_dropped(
        self,
    ) -> None:
        h = self.harness(resync_interval=0.05)
        book = h.stream._book_feed("SPY")
        await book.start()
        socket = await h.ready()
        await socket.emit(raw(KEYFRAME_HEX))
        await asyncio.wait_for(anext(book), 1)
        await socket.emit(raw(UPDATE_2_HEX))  # version 9 while at 7: a gap
        await wait_until(lambda: {"op": "resync", "id": "sdk-1"} in socket.sent)
        await asyncio.sleep(0.08)
        await socket.emit(raw(UPDATE_HEX))
        await wait_until(
            lambda: socket.sent.count({"op": "resync", "id": "sdk-1"}) == 2
        )

    async def test_duplicates_are_ignored_without_a_resync(self) -> None:
        h = self.harness()
        book = h.stream._book_feed("SPY")
        await book.start()
        socket = await h.ready()
        for message in (KEYFRAME_HEX, UPDATE_HEX, UPDATE_HEX, UPDATE_HEX):
            await socket.emit(raw(message))
        await asyncio.wait_for(anext(book), 1)
        self.assertEqual((await asyncio.wait_for(anext(book), 1)).version, 8)
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(anext(book), 0.05)
        self.assertNotIn({"op": "resync", "id": "sdk-1"}, socket.sent)

    async def test_reconnect_mints_a_ticket_resubscribes_and_waits_for_a_keyframe(
        self,
    ) -> None:
        h = self.harness()
        book = h.stream._book_feed("SPY")
        await book.start()
        first = await h.ready()
        await first.emit(raw(KEYFRAME_HEX))
        await asyncio.wait_for(anext(book), 1)

        await first.close()
        second = await h.ready(2)
        self.assertEqual(h.tickets, 2)
        self.assertEqual(second.sent[0]["ticket"], "ticket-2")
        self.assertEqual(second.sent[0]["proto"], 8)
        self.assertEqual(subs(second), subs(first))
        # An update cannot apply to the old book on the new connection.
        await second.emit(raw(UPDATE_HEX))
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(anext(book), 0.05)
        await second.emit(keyframe_version(9))
        self.assertEqual((await asyncio.wait_for(anext(book), 1)).version, 9)

    async def test_terminal_refusals_raise_and_do_not_retry(self) -> None:
        refusals = [
            ("not_entitled_tier", "stream_preview"),
            ("invalid_params", "client_outdated"),
            ("invalid_params", "protobuf_required"),
            ("invalid_params", "proto_required"),
            ("invalid_params", "client_required"),
        ]
        for code, reason in refusals:
            with self.subTest(reason=reason):
                h = self.harness(
                    plans=[[{"op": "nack", "id": None, "code": code, "reason": reason}]]
                )
                book = h.stream._book_feed("SPY")
                await book.start()
                with self.assertRaises(StreamError) as caught:
                    await asyncio.wait_for(anext(book), 1)
                error = caught.exception
                self.assertEqual(
                    (error.code, error.reason, error.terminal), (code, reason, True)
                )
                self.assertIsInstance(error, ITMError)
                await asyncio.sleep(0.05)
                self.assertEqual((h.tickets, len(h.sockets)), (1, 1))
                self.assertTrue(h.sockets[0].closed)

    async def test_refusal_at_the_ticket_mint_is_terminal(self) -> None:
        h = self.harness(
            mints=[
                ITMError(
                    status=403,
                    code="not_entitled_tier",
                    message="not on the preview",
                    details={"reason": "stream_preview"},
                )
            ]
        )
        book = h.stream._book_feed("SPY")
        await book.start()
        with self.assertRaises(StreamError) as caught:
            await asyncio.wait_for(anext(book), 1)
        self.assertEqual(
            (
                caught.exception.reason,
                caught.exception.terminal,
                caught.exception.status,
            ),
            ("stream_preview", True, 403),
        )
        await asyncio.sleep(0.05)
        self.assertEqual((h.tickets, len(h.sockets)), (1, 0))

    async def test_mint_429_waits_for_retry_after(self) -> None:
        h = self.harness(
            mints=[
                ITMError(
                    status=429,
                    code="rate_limited",
                    message="slow down",
                    retry_after_seconds=0.12,
                )
            ],
            min_reconnect=0,
            max_reconnect=0,
        )
        started = time.monotonic()
        book = h.stream._book_feed("SPY")
        await book.start()
        await h.ready()
        self.assertEqual(h.tickets, 2)
        self.assertGreaterEqual(h.connected_at[0] - started, 0.12)

    async def test_connection_limit_backs_off_at_least_the_limit_backoff(self) -> None:
        h = self.harness(
            plans=[
                [
                    {
                        "op": "nack",
                        "id": None,
                        "code": "quota_exceeded",
                        "reason": "connection_limit",
                        "limit": 5,
                    }
                ]
            ],
            connection_limit_backoff=0.1,
            rng=lambda: 0.0,
        )
        book = h.stream._book_feed("SPY")
        await book.start()
        await h.ready(2)
        self.assertGreaterEqual(h.connected_at[1] - h.connected_at[0], 0.1)
        self.assertEqual(h.tickets, 2)
        # Jitter only adds to the minimum.
        h2 = self.harness(
            plans=[
                [
                    {
                        "op": "nack",
                        "id": None,
                        "code": "quota_exceeded",
                        "reason": "connection_limit",
                    }
                ]
            ],
            connection_limit_backoff=0.1,
            rng=lambda: 1.0,
        )
        book2 = h2.stream._book_feed("SPY")
        await book2.start()
        await h2.ready(2)
        self.assertGreaterEqual(h2.connected_at[1] - h2.connected_at[0], 0.15)

    async def test_draining_and_slow_consumer_wait_for_the_hint(self) -> None:
        for code in ("draining", "slow_consumer"):
            with self.subTest(code=code):
                # Without the hint the default wait would be 5 s.
                h = self.harness(min_reconnect=5, max_reconnect=10, rng=lambda: 1.0)
                book = h.stream._book_feed("SPY")
                await book.start()
                first = await h.ready()
                sent_at = time.monotonic()
                await first.emit(
                    json.dumps({"op": "bye", "code": code, "reconnect_after_ms": 120})
                )
                await h.ready(2)
                waited = h.connected_at[1] - sent_at
                self.assertGreaterEqual(waited, 0.12)
                self.assertLess(waited, 2.0)

    async def test_reauth_reconnects_immediately_with_a_new_ticket(self) -> None:
        h = self.harness(min_reconnect=5, max_reconnect=10, rng=lambda: 1.0)
        book = h.stream._book_feed("SPY")
        await book.start()
        first = await h.ready()
        sent_at = time.monotonic()
        await first.emit(
            json.dumps({"op": "bye", "code": "reauth", "reason": "reauth"})
        )
        second = await h.ready(2)
        self.assertLess(h.connected_at[1] - sent_at, 1.0)
        self.assertEqual(second.sent[0]["ticket"], "ticket-2")

    async def test_draining_at_authentication_waits_for_the_hint(self) -> None:
        h = self.harness(
            plans=[[{"op": "bye", "code": "draining", "reconnect_after_ms": 100}]],
            min_reconnect=5,
            max_reconnect=10,
            rng=lambda: 1.0,
        )
        book = h.stream._book_feed("SPY")
        await book.start()
        await h.ready(2)
        self.assertGreaterEqual(h.connected_at[1] - h.connected_at[0], 0.1)

    async def test_network_errors_back_off_exponentially_with_full_jitter(self) -> None:
        draws: list[float] = []

        def full(value: float) -> Any:
            def draw() -> float:
                draws.append(value)
                return value

            return draw

        h = self.harness(
            plans=[OSError("down"), OSError("down")],
            min_reconnect=0.02,
            max_reconnect=0.05,
            rng=full(1.0),
        )
        started = time.monotonic()
        book = h.stream._book_feed("SPY")
        await book.start()
        await h.ready()
        # Two failures: caps 0.02 then 0.04, both at the top of the jitter range.
        self.assertGreaterEqual(h.connected_at[0] - started, 0.06)
        self.assertEqual(len(draws), 2)
        self.assertEqual(h.tickets, 3)

        lucky = self.harness(
            plans=[OSError("down"), OSError("down")],
            min_reconnect=5,
            max_reconnect=10,
            rng=full(0.0),
        )
        lucky_book = lucky.stream._book_feed("SPY")
        await lucky_book.start()
        await lucky.ready()
        self.assertLess(lucky.connected_at[0] - started, 1.0)

    async def test_answers_server_pings_and_sends_its_own(self) -> None:
        h = self.harness(ping_interval=0.05, dead_after=5)
        book = h.stream._book_feed("SPY")
        await book.start()
        socket = await h.ready()
        await socket.emit(json.dumps({"op": "ping"}))
        await wait_until(lambda: {"op": "pong"} in socket.sent)
        await wait_until(lambda: socket.sent.count({"op": "ping"}) >= 2)

    async def test_silence_is_treated_as_a_dead_connection(self) -> None:
        h = self.harness(ping_interval=0.05, dead_after=0.2)
        book = h.stream._book_feed("SPY")
        await book.start()
        first = await h.ready()
        await h.ready(2)
        self.assertTrue(first.closed)
        self.assertEqual(h.tickets, 2)

    async def test_traffic_keeps_a_connection_alive(self) -> None:
        h = self.harness(ping_interval=0.05, dead_after=0.2)
        book = h.stream._book_feed("SPY")
        await book.start()
        socket = await h.ready()
        for _ in range(10):
            await socket.emit(json.dumps({"op": "pong"}))
            await asyncio.sleep(0.05)
        self.assertEqual(len(h.sockets), 1)
        self.assertFalse(socket.closed)

    async def test_one_connection_carries_every_book(self) -> None:
        h = self.harness()
        spy = h.stream._book_feed("SPY")
        qqq = h.stream._book_feed("QQQ")
        await spy.start()
        await qqq.start()
        await wait_until(lambda: len(h.sockets) == 1 and len(subs(h.sockets[0])) == 2)
        socket = h.sockets[0]
        self.assertEqual({m["symbol"] for m in subs(socket)}, {"SPY", "QQQ"})
        self.assertEqual(h.tickets, 1)

        await spy.aclose()
        await wait_until(lambda: {"op": "unsub", "id": "sdk-1"} in socket.sent)
        self.assertFalse(socket.closed)
        await qqq.aclose()
        self.assertTrue(socket.closed)

    async def test_subscription_refusal_fails_only_that_subscription(self) -> None:
        h = self.harness()
        spy = h.stream._book_feed("SPY")
        qqq = h.stream._book_feed("QQQ")
        await spy.start()
        await qqq.start()
        await wait_until(lambda: len(h.sockets) == 1 and len(subs(h.sockets[0])) == 2)
        socket = h.sockets[0]
        await socket.emit(
            json.dumps(
                {
                    "op": "nack",
                    "id": "sdk-1",
                    "code": "quota_exceeded",
                    "reason": "subscription_limit",
                    "limit": 50,
                }
            )
        )
        with self.assertRaises(StreamError) as caught:
            await asyncio.wait_for(anext(spy), 1)
        self.assertEqual(
            (caught.exception.code, caught.exception.reason, caught.exception.details),
            ("quota_exceeded", "subscription_limit", {"limit": 50}),
        )
        await socket.emit(raw(KEYFRAME_HEX.replace("535059", "515151")))
        self.assertEqual((await asyncio.wait_for(anext(qqq), 1)).symbol, "QQQ")


class KeyedClientTests(unittest.IsolatedAsyncioTestCase):
    """The public client decides keyed or not from the credential."""

    def client(
        self,
        api_key: str,
        mint: Any,
        sockets: list[FakeSocket],
        options: list[dict[str, Any]],
    ) -> itm.AsyncITMClient:
        async def connect(_url: str, **kw: Any) -> FakeSocket:
            socket = FakeSocket()
            sockets.append(socket)
            options.append(kw)
            return socket

        return itm.AsyncITMClient(
            api_key=api_key,
            http_transport=httpx.MockTransport(mint),
            stream_connect=connect,
        )

    async def test_an_itm_key_connects_with_protocol_8_and_the_client_name(
        self,
    ) -> None:
        minted: list[str] = []
        sockets: list[FakeSocket] = []
        options: list[dict[str, Any]] = []

        def mint(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v2/stream/ticket")
            self.assertEqual(request.method, "POST")
            minted.append(request.headers["authorization"])
            return httpx.Response(200, json={"data": {"ticket": "tk", "exp": 1}})

        async with (
            self.client("itm_test", mint, sockets, options) as client,
            client.stream._book_feed("SPY") as book,
        ):
            await wait_until(lambda: sockets and subs(sockets[0]))
            auth = sockets[0].sent[0]
            self.assertEqual(
                {k: auth[k] for k in ("op", "ticket", "proto", "encoding")},
                {"op": "auth", "ticket": "tk", "proto": 8, "encoding": "protobuf"},
            )
            self.assertRegex(auth["client"], r"^itmatrix-python/\d+\.\d+\.\d+")
            self.assertEqual(options[0]["subprotocols"], ["itmatrixhq.v1"])
            await sockets[0].emit(raw(KEYFRAME_HEX))
            self.assertEqual((await asyncio.wait_for(anext(book), 1)).version, 7)
        self.assertEqual(minted, ["Bearer itm_test"])

    async def test_other_credentials_cannot_open_a_book(self) -> None:
        sockets: list[FakeSocket] = []
        options: list[dict[str, Any]] = []
        calls = 0

        def mint(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json={"data": {"ticket": "tk", "exp": 1}})

        async with self.client("session-token", mint, sockets, options) as client:
            with self.assertRaises(StreamError) as caught:
                await client.stream._book_feed("SPY").start()
            self.assertEqual(caught.exception.reason, "api_key_required")
            self.assertEqual(calls, 0)
            self.assertFalse(hasattr(client.stream, "subscribe"))
        self.assertEqual(sockets, [])

    async def test_http_429_on_the_mint_is_waited_out(self) -> None:
        sockets: list[FakeSocket] = []
        options: list[dict[str, Any]] = []
        calls: list[float] = []

        def mint(_request: httpx.Request) -> httpx.Response:
            calls.append(time.monotonic())
            if len(calls) == 1:
                return httpx.Response(
                    429,
                    headers={"retry-after": "0.15"},
                    json={"error": {"code": "rate_limited", "message": "slow down"}},
                )
            return httpx.Response(200, json={"data": {"ticket": "tk", "exp": 1}})

        async with (
            self.client("itm_test", mint, sockets, options) as client,
            client.stream._book_feed("SPY"),
        ):
            await wait_until(lambda: sockets and subs(sockets[0]))
        self.assertEqual(len(calls), 2)
        self.assertGreaterEqual(calls[1] - calls[0], 0.15)

    async def test_http_403_stream_preview_is_terminal(self) -> None:
        sockets: list[FakeSocket] = []
        options: list[dict[str, Any]] = []
        calls = 0

        def mint(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                403,
                json={
                    "error": {
                        "code": "not_entitled_tier",
                        "message": "not on the preview",
                        "details": {"reason": "stream_preview"},
                    }
                },
            )

        async with (
            self.client("itm_test", mint, sockets, options) as client,
            client.stream._book_feed("SPY") as book,
        ):
            with self.assertRaises(StreamError) as caught:
                await asyncio.wait_for(anext(book), 1)
            self.assertEqual(caught.exception.reason, "stream_preview")
            self.assertTrue(caught.exception.terminal)
            await asyncio.sleep(0.05)
        self.assertEqual((calls, len(sockets)), (1, 0))


try:
    import websockets
except ModuleNotFoundError:  # the optional itmatrix[stream] extra
    websockets = None  # type: ignore[assignment]


@unittest.skipIf(websockets is None, "needs the itmatrix[stream] extra")
class RealSocketTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_connect_negotiates_the_subprotocol(self) -> None:
        from websockets.asyncio.server import serve

        seen: dict[str, Any] = {}

        async def handler(connection: Any) -> None:
            seen["subprotocol"] = connection.subprotocol
            seen["auth"] = json.loads(await connection.recv())
            await connection.send(json.dumps({"op": "authed"}))
            seen["sub"] = json.loads(await connection.recv())
            await connection.send(raw(KEYFRAME_HEX))
            await connection.send(raw(UPDATE_HEX))
            await connection.wait_closed()

        async def ticket() -> dict[str, Any]:
            return {"ticket": "tk", "exp": 1}

        async with serve(
            handler, "127.0.0.1", 0, subprotocols=["itmatrixhq.v1"]
        ) as server:
            port = server.sockets[0].getsockname()[1]
            stream = Stream(
                url=f"ws://127.0.0.1:{port}/v2/ws",
                ticket=ticket,
                key_auth=True,
                client=CLIENT,
            )
            try:
                async with stream._book_feed("SPY") as book:
                    first = await asyncio.wait_for(anext(book), 5)
                    second = await asyncio.wait_for(anext(book), 5)
            finally:
                await stream.close()
        self.assertEqual(seen["subprotocol"], "itmatrixhq.v1")
        self.assertEqual(seen["auth"]["proto"], 8)
        self.assertEqual(seen["auth"]["client"], CLIENT)
        self.assertEqual(seen["sub"]["symbol"], "SPY")
        self.assertEqual((first.version, second.version), (7, 8))


if __name__ == "__main__":
    unittest.main()
