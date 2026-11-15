"""Live GEX books: the object users hold (client.stream.gex)."""

import asyncio
import os
import signal
import threading
import time
import unittest
from typing import Any

import httpx
from test_stream import (
    KEYFRAME_HEX,
    UPDATE_2_HEX,
    UPDATE_HEX,
    FakeSocket,
    Harness,
    raw,
    subs,
    wait_until,
)

import itmatrix as itm
from itmatrix._core.stream import StreamError


class LiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        for harness in getattr(self, "harnesses", []):
            await harness.stream.close()

    def harness(self, *args: Any, **kwargs: Any) -> Harness:
        harness = Harness(*args, **kwargs)
        self.harnesses = [*getattr(self, "harnesses", []), harness]
        return harness

    async def test_returns_at_once_and_emits_books_to_handlers(self) -> None:
        h = self.harness()
        spy = h.stream.gex("spy")
        self.assertIsNone(spy.latest)
        books: list[itm.Book] = []
        statuses: list[str] = []
        spy.on("update", books.append)
        spy.on("status", statuses.append)
        socket = await h.ready()
        self.assertEqual(subs(socket), [{"op": "sub", "id": "sdk-1", "topic": "gex", "symbol": "SPY"}])

        await socket.emit(raw(KEYFRAME_HEX))
        first = await spy.ready(1)
        self.assertIs(spy.latest, first)
        self.assertEqual((first.symbol, first.spot, len(first)), ("SPY", 601.25, 5))
        rows = list(first)
        self.assertEqual(rows[0], itm.GexRow(595.0, rows[0].call, rows[0].put, rows[0].total))
        self.assertEqual([r.strike for r in first], [595.0, 600.0, 602.5, 605.0, 610.0])
        self.assertEqual(first.meta.reason, "subscribe")
        self.assertEqual(first.meta.version, 7)

        await socket.emit(raw(UPDATE_HEX))
        await wait_until(lambda: len(books) == 2)
        second = books[1]
        self.assertEqual(second.meta.changed, (600.0, 610.0))
        self.assertEqual(second.get(610).total, 28_125_000.0)  # type: ignore[union-attr]
        self.assertIsNone(second.get(611))
        self.assertIsNone(second.meta.reason)
        self.assertEqual(statuses, ["live"])
        spy.close()
        await wait_until(lambda: {"op": "unsub", "id": "sdk-1"} in socket.sent)
        self.assertEqual(statuses, ["live", "closed"])
        self.assertEqual(spy.status, "closed")

    async def test_filters_apply_to_every_book_and_update_re_emits_at_once(self) -> None:
        h = self.harness()
        spy = h.stream.gex("SPY", nearest=2)
        self.assertEqual(spy.filter, {"nearest": 2})
        books: list[itm.Book] = []
        spy.on("update", books.append)
        socket = await h.ready()
        await socket.emit(raw(KEYFRAME_HEX))
        first = await spy.ready(1)
        self.assertEqual(first.strikes, (600.0, 602.5))  # 1.25 from spot either side

        spy.update(top=1, by="put")
        self.assertEqual(spy.latest.strikes, (595.0,))  # type: ignore[union-attr]
        self.assertEqual(books[-1].strikes, (595.0,))
        spy.update(within=0.005, top=1)
        self.assertEqual(spy.filter, {"within": 0.005, "top": 1, "by": "gross"})
        spy.update()
        self.assertEqual(len(spy.latest), 5)  # type: ignore[arg-type]
        self.assertEqual(len(subs(socket)), 1)  # the server subscription never changed

    async def test_filter_arguments_are_checked_when_called(self) -> None:
        h = self.harness()
        for kwargs, error in (
            ({"nearest": 5, "within": 0.1}, ValueError),
            ({"by": "net"}, ValueError),
            ({"top": 3, "by": "loud"}, ValueError),
            ({"nearest": "20"}, TypeError),
            ({"within": -1}, ValueError),
            ({"between": (610, 590)}, ValueError),
            ({"between": 600}, TypeError),
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(error):
                h.stream.gex("SPY", **kwargs)
        with self.assertRaises(ValueError):
            h.stream.gex("  ")
        spy = h.stream.gex("SPY")
        with self.assertRaises(TypeError):
            spy.update(nearby=3)
        with self.assertRaises(ValueError):
            spy.on("tick", print)  # type: ignore[arg-type]

    async def test_async_for_is_conflated_to_the_newest_book(self) -> None:
        h = self.harness()
        spy = h.stream.gex("SPY")
        socket = await h.ready()
        for message in (KEYFRAME_HEX, UPDATE_HEX, UPDATE_2_HEX):
            await socket.emit(raw(message))
        await wait_until(lambda: spy.latest is not None and spy.latest.meta.version == 9)
        seen = []
        async for book in spy:
            seen.append(book.meta.version)
            break
        self.assertEqual(seen, [9])

    async def test_a_slow_async_handler_gets_the_newest_book_not_a_backlog(self) -> None:
        h = self.harness()
        spy = h.stream.gex("SPY")
        gate = asyncio.Event()
        seen: list[int] = []

        async def slow(book: itm.Book) -> None:
            seen.append(book.meta.version)
            await gate.wait()

        spy.on("update", slow)
        socket = await h.ready()
        await socket.emit(raw(KEYFRAME_HEX))
        await wait_until(lambda: seen == [7])
        await socket.emit(raw(UPDATE_HEX))
        await socket.emit(raw(UPDATE_2_HEX))
        await wait_until(lambda: spy.latest is not None and spy.latest.meta.version == 9)
        gate.set()
        await wait_until(lambda: seen == [7, 9])

    async def test_a_raising_handler_reports_an_error_and_the_stream_goes_on(self) -> None:
        h = self.harness()
        spy = h.stream.gex("SPY")
        errors: list[BaseException] = []
        versions: list[int] = []

        def broken(_book: itm.Book) -> None:
            raise RuntimeError("handler bug")

        spy.on("update", broken)
        spy.on("update", lambda b: versions.append(b.meta.version))
        spy.on("error", errors.append)
        socket = await h.ready()
        await socket.emit(raw(KEYFRAME_HEX))
        await socket.emit(raw(UPDATE_HEX))
        await wait_until(lambda: versions == [7, 8])
        self.assertEqual([str(e) for e in errors], ["handler bug", "handler bug"])
        self.assertEqual(spy.status, "live")

    async def test_each_symbol_is_its_own_object_on_one_connection(self) -> None:
        h = self.harness()
        with self.assertRaises(TypeError):
            h.stream.gex(["SPY", "QQQ"])  # type: ignore[arg-type]
        spy = h.stream.gex("SPY")
        qqq = h.stream.gex("qqq")
        socket = await h.ready()
        await wait_until(lambda: len(subs(socket)) == 2)
        self.assertEqual([s["symbol"] for s in subs(socket)], ["SPY", "QQQ"])
        await socket.emit(raw(KEYFRAME_HEX))
        await spy.ready(1)
        self.assertIsNone(qqq.latest)
        self.assertEqual(len(h.sockets), 1)
        # The socket closes when the last object on it closes.
        await spy.aclose()
        self.assertFalse(socket.closed)
        await qqq.aclose()
        await wait_until(lambda: socket.closed)

    async def test_changed_waits_for_the_next_book_and_age_reads_now(self) -> None:
        h = self.harness()
        spy = h.stream.gex("SPY")
        self.assertIsNone(spy.age)
        socket = await h.ready()
        await socket.emit(raw(KEYFRAME_HEX))
        first = await spy.ready(1)
        self.assertIs(await spy.ready(1), first)  # ready() returns latest at once
        with self.assertRaises(asyncio.TimeoutError):
            await spy.changed(0.05)
        waiting = asyncio.ensure_future(spy.changed(1))
        await asyncio.sleep(0)
        await socket.emit(raw(UPDATE_HEX))
        self.assertEqual((await waiting).meta.version, 8)
        self.assertGreater(spy.age, 0)  # the vector's capture time is in the past
        async with spy:
            pass
        self.assertTrue(spy.closed)
        with self.assertRaises(RuntimeError):
            await spy.changed(1)

    async def test_reconnect_reports_status_and_recovers(self) -> None:
        h = self.harness()
        spy = h.stream.gex("SPY")
        statuses: list[str] = []
        spy.on("status", statuses.append)
        first = await h.ready()
        await first.emit(raw(KEYFRAME_HEX))
        await spy.ready(1)
        await first.close()
        second = await h.ready(2)
        await wait_until(lambda: statuses == ["live", "reconnecting", "live"])
        await second.emit(raw(KEYFRAME_HEX))
        await wait_until(lambda: spy.latest is not None)
        self.assertEqual(spy.status, "live")

    async def test_terminal_refusal_is_an_error_then_closed(self) -> None:
        h = self.harness(
            plans=[[{"op": "nack", "id": None, "code": "invalid_params", "reason": "client_outdated"}]]
        )
        spy = h.stream.gex("SPY")
        errors: list[BaseException] = []
        spy.on("error", errors.append)
        with self.assertRaises(StreamError) as caught:
            await spy.ready(1)
        self.assertEqual(caught.exception.reason, "client_outdated")
        self.assertEqual(spy.status, "closed")
        self.assertEqual(errors, [caught.exception])
        with self.assertRaises(StreamError):
            async for _ in spy:
                pass

    async def test_a_token_session_gets_api_key_required(self) -> None:
        async def ticket() -> dict[str, Any]:
            raise AssertionError("no ticket is minted")

        stream = itm.Stream(url="wss://stream.test/v2/ws", ticket=ticket)
        spy = stream.gex("SPY")
        with self.assertRaises(StreamError) as caught:
            await spy.ready(1)
        self.assertEqual(caught.exception.reason, "api_key_required")

    async def test_book_frame_and_repr(self) -> None:
        h = self.harness()
        spy = h.stream.gex("SPY")
        socket = await h.ready()
        await socket.emit(raw(KEYFRAME_HEX))
        book = await spy.ready(1)
        self.assertIn("Book(SPY spot=601.25", repr(book))
        self.assertEqual(book.totals, tuple(r.total for r in book))
        frame = book.df()
        self.assertEqual(list(frame.columns), ["strike", "call", "put", "total"])
        self.assertEqual(len(frame), 5)


class RunTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        for harness in getattr(self, "harnesses", []):
            await harness.stream.close()

    def harness(self) -> Harness:
        harness = Harness()
        self.harnesses = [*getattr(self, "harnesses", []), harness]
        return harness

    async def test_run_returns_at_once_with_no_live_book(self) -> None:
        h = self.harness()
        await asyncio.wait_for(h.stream.run(), 1)

    async def test_run_returns_when_the_last_live_book_closes(self) -> None:
        h = self.harness()
        spy, qqq = h.stream.gex("SPY"), h.stream.gex("QQQ")
        await h.ready()
        running = asyncio.create_task(h.stream.run())
        spy.close()
        await asyncio.sleep(0.05)
        self.assertFalse(running.done(), "one book is still open")
        qqq.close()
        await asyncio.wait_for(running, 1)

    async def test_cancelling_run_closes_every_live_book_and_re_raises(self) -> None:
        h = self.harness()
        spy, qqq = h.stream.gex("SPY"), h.stream.gex("QQQ")
        await h.ready()
        running = asyncio.create_task(h.stream.run())
        await asyncio.sleep(0.02)
        running.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await running
        self.assertTrue(spy.closed and qqq.closed)
        self.assertEqual((spy.status, qqq.status), ("closed", "closed"))

    async def test_ctrl_c_closes_every_live_book_and_run_returns(self) -> None:
        if os.name != "posix":
            self.skipTest("SIGINT routing needs a POSIX event loop")
        h = self.harness()
        spy = h.stream.gex("SPY")
        await h.ready()
        asyncio.get_running_loop().call_later(0.05, os.kill, os.getpid(), signal.SIGINT)
        await asyncio.wait_for(h.stream.run(), 2)
        self.assertTrue(spy.closed)

    async def test_the_async_client_runs_its_stream(self) -> None:
        client = itm.AsyncITMClient(api_key="itm_test")
        try:
            await asyncio.wait_for(client.run(), 1)  # nothing open: returns
        finally:
            await client.aclose()


class SyncLiveTests(unittest.TestCase):
    def test_blocking_client_runs_handlers_on_their_own_thread(self) -> None:
        sockets: list[FakeSocket] = []

        def mint(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": {"ticket": "tk", "exp": 1}})

        async def connect(_url: str, **_kw: Any) -> FakeSocket:
            socket = FakeSocket()
            sockets.append(socket)
            return socket

        client = itm.ITMClient(api_key="itm_test", http_transport=httpx.MockTransport(mint))
        client._runtime.client.stream._connect = connect
        portal = client._runtime.portal
        try:
            seen: list[tuple[int, str]] = []
            done = threading.Event()

            def on_book(book: itm.Book) -> None:
                seen.append((book.meta.version, threading.current_thread().name))
                if book.meta.version == 8:
                    done.set()

            spy = client.stream.gex("SPY", nearest=3)
            spy.on("update", on_book)
            deadline = time.monotonic() + 3
            while not (sockets and subs(sockets[0])):
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.005)
            portal.call(sockets[0].emit, raw(KEYFRAME_HEX))
            book = spy.ready(2)
            self.assertEqual(len(book), 3)
            portal.call(sockets[0].emit, raw(UPDATE_HEX))
            self.assertTrue(done.wait(2))
            self.assertEqual({name for _, name in seen}, {"itmatrix-gex-handlers"})
            self.assertEqual(spy.latest.meta.version, 8)  # type: ignore[union-attr]
            with self.assertRaises(TypeError):

                async def handler(_book: itm.Book) -> None: ...

                spy.on("update", handler)
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(1, thread_name_prefix="mine") as pool:
                names: list[str] = []
                spy.on("update", lambda _b: names.append(threading.current_thread().name), executor=pool)
                spy.update(nearest=2)
                deadline = time.monotonic() + 2
                while not names:
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.005)
                self.assertTrue(names[0].startswith("mine"))
            with ThreadPoolExecutor(1) as waiter:
                nxt = waiter.submit(spy.changed, 2)
                time.sleep(0.05)
                portal.call(sockets[0].emit, raw(UPDATE_2_HEX))
                self.assertEqual(nxt.result().meta.version, 9)
            spy.update(nearest=1)
            self.assertEqual(len(spy.latest), 1)  # type: ignore[arg-type]
            spy.close()
            self.assertEqual(spy.status, "closed")
            with self.assertRaises(RuntimeError):
                spy.changed(1)
        finally:
            client.close()

    def test_blocking_run_returns_when_the_last_book_closes(self) -> None:
        sockets: list[FakeSocket] = []

        def mint(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": {"ticket": "tk", "exp": 1}})

        async def connect(_url: str, **_kw: Any) -> FakeSocket:
            socket = FakeSocket()
            sockets.append(socket)
            return socket

        client = itm.ITMClient(api_key="itm_test", http_transport=httpx.MockTransport(mint))
        client._runtime.client.stream._connect = connect
        try:
            started = time.monotonic()
            client.run()  # nothing open: returns at once
            self.assertLess(time.monotonic() - started, 0.5)
            spy = client.stream.gex("SPY")
            timer = threading.Timer(0.1, spy.close)
            timer.start()
            started = time.monotonic()
            client.run()
            self.assertGreaterEqual(time.monotonic() - started, 0.05)
            self.assertTrue(spy.closed)
            timer.join()
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
