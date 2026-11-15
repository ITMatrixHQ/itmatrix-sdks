"""Live GEX books: a long-lived object per symbol that keeps the newest book.

``client.stream.gex("SPY", nearest=20)`` returns a :class:`LiveGex` at once and
connects in the background. Read ``latest`` any time, register handlers with
``on("update" | "error" | "status", fn)``, ``await changed()`` for the next book,
or ``async for book in live``. Every path is conflated: a slow reader or handler
gets the newest book, never a backlog. All live objects of one client share one
connection, and each symbol is its own object with no ordering across symbols.
"""

from __future__ import annotations

import asyncio
import bisect
import collections
import inspect
import threading
import warnings
from collections.abc import AsyncIterator, Callable, Iterator
from concurrent.futures import Executor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

from typing_extensions import Self

from . import filters as _filters
from .gex_book import GexBookState

if TYPE_CHECKING:
    from .stream import Stream, _BookFeed

Event = Literal["update", "error", "status"]
Status = Literal["connecting", "live", "reconnecting", "closed"]
_EVENTS = ("update", "error", "status")


class GexRow(NamedTuple):
    """One strike of a book: dollars of dealer hedging per $1 move.

    ``call`` is positive, ``put`` negative, ``total`` is their sum.
    """

    strike: float
    call: float
    put: float
    total: float


@dataclass(frozen=True, slots=True)
class BookMeta:
    """Bookkeeping you rarely need.

    ``changed`` holds the strikes whose values moved in the frame behind this
    book (every strike for a full book). ``reason`` says why the server sent a
    full book (``"subscribe"``, ``"periodic"``, ``"resync"``, ``"rebuild"`` or
    ``"recovery"``) and is ``None`` for an update.
    """

    book_id: int
    version: int
    changed: tuple[float, ...]
    reason: str | None


class Book:
    """One immutable GEX book for one symbol.

    Iterate it for :class:`GexRow` rows in strike order, or read the columns
    ``strikes``, ``calls``, ``puts`` and ``totals``. ``net_gex`` is the
    whole-chain total, not the sum of the listed strikes.
    """

    __slots__ = ("_rows", "_state")

    def __init__(self, state: GexBookState) -> None:
        self._state = state
        self._rows: tuple[GexRow, ...] | None = None

    @property
    def symbol(self) -> str:
        return self._state.symbol

    @property
    def spot(self) -> float:
        return self._state.spot

    @property
    def net_gex(self) -> float:
        return self._state.net_gex

    @property
    def flip(self) -> float | None:
        """The zero-gamma strike, or ``None`` when the chain has none."""
        return self._state.flip_point

    @property
    def time(self) -> datetime:
        """When the book was computed (UTC)."""
        return self._state.captured_at

    @property
    def age(self) -> float:
        """Seconds since the book was computed, read now."""
        return (datetime.now(timezone.utc) - self._state.captured_at).total_seconds()

    @property
    def strikes(self) -> tuple[float, ...]:
        return self._state.strikes

    @property
    def calls(self) -> tuple[float, ...]:
        return self._state.call_gex

    @property
    def puts(self) -> tuple[float, ...]:
        return self._state.put_gex

    @property
    def totals(self) -> tuple[float, ...]:
        return self._state.gex

    @property
    def rows(self) -> tuple[GexRow, ...]:
        rows = self._rows
        if rows is None:
            s = self._state
            rows = tuple(map(GexRow, s.strikes, s.call_gex, s.put_gex, s.gex))
            self._rows = rows
        return rows

    @property
    def meta(self) -> BookMeta:
        s = self._state
        return BookMeta(
            book_id=s.book_id,
            version=s.version,
            changed=tuple(s.strikes[i] for i in s.changed),
            reason=s.reason,
        )

    def __len__(self) -> int:
        return len(self._state.strikes)

    def __iter__(self) -> Iterator[GexRow]:
        return iter(self.rows)

    def __getitem__(self, index: int) -> GexRow:
        return self.rows[index]

    def get(self, strike: float) -> GexRow | None:
        """The row at ``strike`` dollars, or ``None`` when the book does not hold it."""
        key = round(strike * 1000)
        thousandths = self._state.strikes_thousandths
        at = bisect.bisect_left(thousandths, key)
        if at < len(thousandths) and thousandths[at] == key:
            return self.rows[at]
        return None

    def df(self) -> Any:
        """The rows as a pandas DataFrame with columns strike, call, put, total."""
        try:
            import pandas
        except ModuleNotFoundError as error:
            raise ImportError("Book.df() needs pandas: pip install pandas") from error
        s = self._state
        return pandas.DataFrame(
            {"strike": s.strikes, "call": s.call_gex, "put": s.put_gex, "total": s.gex}
        )

    def __repr__(self) -> str:
        flip = "None" if self.flip is None else f"{self.flip:g}"
        return (
            f"Book({self.symbol} spot={self.spot:g} net_gex={self.net_gex:,.0f} "
            f"flip={flip} strikes={len(self)} at {self.time:%H:%M:%S})"
        )


Handler = Callable[[Any], Any]


def _report(error: BaseException) -> None:
    warnings.warn(f"itmatrix: an error handler raised {error!r}", RuntimeWarning, stacklevel=3)


class _Dispatch:
    """Calls handlers. Subclasses decide where they run and how they conflate.

    ``update`` is always conflated: a handler that is still busy next gets the
    newest book. ``status`` and ``error`` events are delivered in order.
    """

    def __init__(self) -> None:
        self.handlers: dict[str, tuple[tuple[Handler, Any], ...]] = {e: () for e in _EVENTS}
        self.lock = threading.Lock()

    def add(self, event: str, handler: Handler, executor: Any = None) -> None:
        with self.lock:
            self.handlers[event] = (*self.handlers[event], (handler, executor))

    def remove(self, event: str, handler: Handler) -> None:
        with self.lock:
            kept = [entry for entry in self.handlers[event] if entry[0] is not handler]
            self.handlers[event] = tuple(kept)

    def emit(self, event: str, value: Any) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def close(self) -> None:
        pass

    def _failed(self, event: str, error: Exception) -> None:
        if event == "error":
            _report(error)
        else:
            self.emit("error", error)


class _LoopDispatch(_Dispatch):
    """For the async client: handlers run on the event loop.

    A plain function is called inline, so keep it quick. A coroutine function
    runs as one task at a time per handler.
    """

    def __init__(self) -> None:
        super().__init__()
        self._running: set[Handler] = set()
        self._pending: dict[Handler, Any] = {}

    def add(self, event: str, handler: Handler, executor: Any = None) -> None:
        if executor is not None:
            raise TypeError("executor applies to ITMClient; AsyncITMClient handlers run on the loop")
        super().add(event, handler)

    def emit(self, event: str, value: Any) -> None:
        for handler, _ in self.handlers[event]:
            if inspect.iscoroutinefunction(handler):
                self._schedule(event, handler, value)
            else:
                try:
                    handler(value)
                except Exception as error:  # noqa: BLE001
                    self._failed(event, error)

    def _schedule(self, event: str, handler: Handler, value: Any) -> None:
        if event == "update":
            if handler in self._running:
                self._pending[handler] = value  # conflate: keep only the newest
                return
            self._running.add(handler)
        asyncio.get_running_loop().create_task(self._run(event, handler, value))

    async def _run(self, event: str, handler: Handler, value: Any) -> None:
        try:
            while True:
                try:
                    await handler(value)
                except Exception as error:  # noqa: BLE001
                    self._failed(event, error)
                if event != "update" or handler not in self._pending:
                    return
                value = self._pending.pop(handler)
        finally:
            if event == "update":
                self._running.discard(handler)

    def close(self) -> None:
        self._pending.clear()


class _ThreadDispatch(_Dispatch):
    """For the blocking client: the network loop never waits for a handler.

    Events are handed to one dispatcher thread per live object. A handler with
    no ``executor`` runs on that thread. A handler with an executor is
    submitted to it, one call in flight per handler.
    """

    def __init__(self) -> None:
        super().__init__()
        self._cond = threading.Condition()
        self._update: Any = None
        self._events: collections.deque[tuple[str, Any]] = collections.deque(maxlen=256)
        self._closing = False
        self._busy: set[Handler] = set()
        self._pending: dict[Handler, Any] = {}
        self._thread = threading.Thread(target=self._loop, name="itmatrix-gex-handlers", daemon=True)
        self._thread.start()

    def add(self, event: str, handler: Handler, executor: Any = None) -> None:
        if inspect.iscoroutinefunction(handler):
            raise TypeError(
                "ITMClient runs handlers on threads: pass a plain function, "
                "or use AsyncITMClient for async handlers"
            )
        if executor is not None and not isinstance(executor, Executor):
            raise TypeError("executor must be a concurrent.futures.Executor")
        super().add(event, handler, executor)

    def emit(self, event: str, value: Any) -> None:
        with self._cond:
            if event == "update":
                self._update = value
            else:
                self._events.append((event, value))
            self._cond.notify()

    def _loop(self) -> None:
        while True:
            with self._cond:
                while self._update is None and not self._events and not self._closing:
                    self._cond.wait()
                if self._events:
                    event, value = self._events.popleft()
                elif self._update is not None:
                    event, value, self._update = "update", self._update, None
                else:
                    return
            for handler, executor in self.handlers[event]:
                if executor is None:
                    self._call(event, handler, value)
                else:
                    self._submit(event, handler, executor, value)

    def _call(self, event: str, handler: Handler, value: Any) -> None:
        try:
            handler(value)
        except Exception as error:  # noqa: BLE001
            self._failed(event, error)

    def _submit(self, event: str, handler: Handler, executor: Executor, value: Any) -> None:
        if event == "update":
            with self.lock:
                if handler in self._busy:
                    self._pending[handler] = value
                    return
                self._busy.add(handler)

        def run(value: Any = value) -> None:
            while True:
                self._call(event, handler, value)
                if event != "update":
                    return
                with self.lock:
                    if handler not in self._pending:
                        self._busy.discard(handler)
                        return
                    value = self._pending.pop(handler)

        try:
            executor.submit(run)
        except RuntimeError as error:  # the executor was shut down
            with self.lock:
                self._busy.discard(handler)
            self._failed(event, error)

    def close(self) -> None:
        with self._cond:
            self._closing = True
            self._cond.notify()


class _Watcher:
    """One ``async for`` over a live object: holds only the newest unread book."""

    def __init__(self) -> None:
        self.book: Book | None = None
        self.error: BaseException | None = None
        self.done = False
        self.event = asyncio.Event()

    def push(self, book: Book) -> None:
        self.book = book
        self.event.set()

    def end(self, error: BaseException | None) -> None:
        self.error = error
        self.done = True
        self.event.set()


class _WatchIterator(AsyncIterator[Book]):
    def __init__(self, live: LiveGex, watcher: _Watcher) -> None:
        self._live = live
        self._watcher = watcher

    def __aiter__(self) -> _WatchIterator:
        return self

    async def __anext__(self) -> Book:
        w = self._watcher
        while True:
            if w.book is not None:
                book, w.book = w.book, None
                w.event.clear()
                return book
            if w.done:
                self._live._watchers.discard(w)
                if w.error is not None:
                    raise w.error
                raise StopAsyncIteration
            w.event.clear()
            await w.event.wait()


class LiveGex:
    """The live GEX book of one symbol, kept current in the background.

    Created by ``client.stream.gex(...)``. ``latest`` is the newest
    :class:`Book` (``None`` before the first) and ``age`` its age in seconds.
    Handlers registered with :meth:`on` get ``"update"`` (a :class:`Book`),
    ``"error"`` (an exception) and ``"status"`` (``"connecting"``, ``"live"``,
    ``"reconnecting"`` or ``"closed"``). A handler that raises is reported to
    the ``"error"`` handlers and never stops the stream. A terminal refusal (for
    example ``client_outdated``) is an ``"error"`` with a :class:`StreamError`,
    then the status becomes ``"closed"``.

    Conflation is the contract: ``latest``, handlers, :meth:`changed` and
    ``async for`` always get the newest book. Nothing builds a backlog.
    """

    def __init__(
        self,
        stream: Stream,
        symbol: str,
        options: dict[str, Any],
        dispatch: _Dispatch | None = None,
    ) -> None:
        self._filter, self._options = _filters.build(options)
        self._stream = stream
        self._dispatch = dispatch or _LoopDispatch()
        self.symbol = symbol
        self.latest: Book | None = None
        self.status: Status = "connecting"
        self.error: BaseException | None = None
        self._feed: _BookFeed = stream._book_feed(symbol, self)
        self._watchers: set[_Watcher] = set()
        self._waiters: list[asyncio.Future[Book]] = []
        self._started = False
        self._closed = False
        stream._live_add(self)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop yet: start on the first await
        self._start()

    # -- public ---------------------------------------------------------

    @property
    def age(self) -> float | None:
        """Seconds since ``latest`` was computed, or ``None`` before the first book."""
        latest = self.latest
        return None if latest is None else latest.age

    @property
    def filter(self) -> dict[str, Any]:
        """The filter options in force, normalized (empty for the whole book)."""
        return dict(self._options)

    @property
    def closed(self) -> bool:
        return self._closed

    def on(self, event: Event, handler: Handler, *, executor: Executor | None = None) -> Callable[[], None]:
        """Call ``handler`` on every ``event``; returns a function that removes it.

        On ``AsyncITMClient`` the handler may be a coroutine function. A plain
        function runs inline on the event loop, so keep it quick. On
        ``ITMClient`` see :class:`SyncLiveGex` for where handlers run.
        """
        if event not in _EVENTS:
            raise ValueError(f"event must be one of {', '.join(_EVENTS)}, not {event!r}")
        if not callable(handler):
            raise TypeError("handler must be callable")
        self._dispatch.add(event, handler, executor)
        return lambda: self._dispatch.remove(event, handler)

    def off(self, event: Event, handler: Handler) -> None:
        self._dispatch.remove(event, handler)

    def update(self, **filters: Any) -> None:
        """Replace the filter, for example ``update(nearest=40)``.

        ``update()`` with no arguments shows the whole book. The book already
        held is re-filtered and emitted at once.
        """
        self._filter, self._options = _filters.build(filters)
        state = self._feed.book.state
        if state is not None:
            self._publish(state)

    async def ready(self, timeout: float | None = None) -> Book:
        """The first book: ``latest`` if there is one, else wait for it."""
        if self.latest is not None:
            return self.latest
        return await self.changed(timeout)

    async def changed(self, timeout: float | None = None) -> Book:
        """Wait for the next book after now. Raises the stream's error if it fails."""
        self._start()
        if self.error is not None:
            raise self.error
        if self._closed:
            raise RuntimeError(f"the live GEX book for {self.symbol} is closed")
        waiter: asyncio.Future[Book] = asyncio.get_running_loop().create_future()
        self._waiters.append(waiter)
        try:
            return await asyncio.wait_for(waiter, timeout)
        finally:
            if waiter in self._waiters:
                self._waiters.remove(waiter)

    def close(self) -> None:
        """Stop and release the subscription. Safe to call more than once."""
        if self._closed:
            return
        self._finish(None)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._feed.aclose())

    async def aclose(self) -> None:
        """:meth:`close`, then wait until the subscription is released."""
        self._finish(None)
        await self._feed.aclose()

    def __aiter__(self) -> AsyncIterator[Book]:
        """Books as they arrive. Conflated: you get the newest unread book."""
        self._start()
        watcher = _Watcher()
        if self._closed:
            watcher.end(self.error)
        else:
            self._watchers.add(watcher)
            if self.latest is not None:
                watcher.push(self.latest)
        return _WatchIterator(self, watcher)

    async def __aenter__(self) -> Self:
        self._start()
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    def __repr__(self) -> str:
        return f"LiveGex({self.symbol} {self.status} filter={self._options})"

    # -- feed side (event loop) -------------------------------------------

    def _start(self) -> None:
        if self._started:
            return
        self._started = True
        self._dispatch.emit("status", "connecting")
        asyncio.get_running_loop().create_task(self._open())

    async def _open(self) -> None:
        if self._closed:
            return
        try:
            await self._stream._start(self._feed)
        except Exception as error:  # noqa: BLE001
            self._fail(error)

    def _publish(self, state: GexBookState) -> None:
        if self._closed:
            return
        if self._filter is not None:
            try:
                state = self._filter(state)
            except Exception as error:  # noqa: BLE001
                self._dispatch.emit("error", error)
                return
        book = Book(state)
        self.latest = book
        waiters, self._waiters = self._waiters, []
        for waiter in waiters:
            if not waiter.done():
                waiter.set_result(book)
        for watcher in self._watchers:
            watcher.push(book)
        self._dispatch.emit("update", book)

    def _connected(self) -> None:
        self._set_status("live")

    def _disconnected(self) -> None:
        if not self._closed:
            self._set_status("reconnecting")

    def _fail(self, error: BaseException) -> None:
        if self._closed:
            return
        self.error = error
        self._dispatch.emit("error", error)
        self._finish(error)
        try:
            asyncio.get_running_loop().create_task(self._feed.aclose())
        except RuntimeError:
            pass

    def _finish(self, error: BaseException | None) -> None:
        if self._closed:
            return
        self._closed = True
        waiters, self._waiters = self._waiters, []
        for waiter in waiters:
            if not waiter.done():
                if error is not None:
                    waiter.set_exception(error)
                else:
                    waiter.set_exception(RuntimeError(f"the live GEX book for {self.symbol} is closed"))
        for watcher in self._watchers:
            watcher.end(error)
        self._set_status("closed")
        self._dispatch.close()
        self._stream._live_done(self)

    def _set_status(self, status: Status) -> None:
        if status == self.status:
            return
        self.status = status
        self._dispatch.emit("status", status)


class SyncLiveGex:
    """:class:`LiveGex` for the blocking :class:`~itmatrix.ITMClient`.

    The connection runs on the client's background loop. ``latest``, ``age``
    and ``status`` can be read from any thread, and :meth:`ready` and
    :meth:`changed` block until a book arrives.

    Handlers never run on the network loop. By default they run one at a time
    on this object's own handler thread (named ``itmatrix-gex-handlers``).
    Pass ``executor=`` to :meth:`on` to run a handler on a thread pool you own
    instead. Either way a busy handler is next called with the newest book.
    """

    def __init__(self, portal: Any, live: LiveGex, dispatch: _ThreadDispatch) -> None:
        self._portal = portal
        self._live = live
        self._dispatch = dispatch
        self._cond = threading.Condition()
        self._count = 0
        self._next: Book | None = None
        dispatch.add("update", self._seen)
        dispatch.add("status", self._status)

    def _seen(self, book: Book) -> None:
        with self._cond:
            self._count += 1
            self._next = book
            self._cond.notify_all()

    def _status(self, status: str) -> None:
        if status == "closed":
            with self._cond:
                self._cond.notify_all()

    @property
    def symbol(self) -> str:
        return self._live.symbol

    @property
    def latest(self) -> Book | None:
        return self._live.latest

    @property
    def age(self) -> float | None:
        return self._live.age

    @property
    def status(self) -> Status:
        return self._live.status

    @property
    def error(self) -> BaseException | None:
        return self._live.error

    @property
    def closed(self) -> bool:
        return self._live.closed

    @property
    def filter(self) -> dict[str, Any]:
        return self._live.filter

    def on(self, event: Event, handler: Handler, *, executor: Executor | None = None) -> Callable[[], None]:
        """Call ``handler`` for every ``event``: on this object's handler thread,
        or on ``executor`` when you pass one. Returns a function that removes it."""
        return self._live.on(event, handler, executor=executor)

    def off(self, event: Event, handler: Handler) -> None:
        self._live.off(event, handler)

    def update(self, **filters: Any) -> None:
        """Replace the filter, for example ``update(nearest=40)``."""
        _filters.build(filters)  # validate in the caller's thread
        self._portal.call(lambda: (self._live.update(**filters),))

    def ready(self, timeout: float | None = None) -> Book:
        """The first book: ``latest`` if there is one, else block for it."""
        latest = self._live.latest
        return latest if latest is not None else self.changed(timeout)

    def changed(self, timeout: float | None = None) -> Book:
        """Block for the next book after now. Raises the stream's error if it fails."""
        with self._cond:
            seen = self._count
            if not self._cond.wait_for(
                lambda: self._count != seen or self._live.closed, timeout
            ):
                raise TimeoutError(f"no new GEX book for {self.symbol}")
            if self._count != seen and self._next is not None:
                return self._next
        raise self._live.error or RuntimeError(f"the live GEX book for {self.symbol} is closed")

    def close(self) -> None:
        """Stop and release the subscription. Safe to call more than once."""
        if self._live.closed:
            return
        try:
            self._portal.call(self._live.aclose)
        except RuntimeError:
            pass  # the client is already closed

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return repr(self._live)
