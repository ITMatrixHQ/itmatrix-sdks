"""Discoverable clients sharing one public transport implementation."""

from __future__ import annotations

import contextlib
import contextvars
import sys
import weakref
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import replace
from datetime import datetime
from functools import partial, wraps
from threading import RLock, Thread, get_ident
from typing import Any, Literal, TypeVar
from urllib.parse import quote

import anyio
import httpx
from anyio.from_thread import BlockingPortal
from typing_extensions import Self, Unpack

from ._core.client import ITMClient as _TransportClient
from ._core.config import DEFAULT_BASE_URL
from ._core.models import (
    Credential,
    GexHistoryData,
    GexReferenceData,
    Result,
    Transport,
)
from .analytics import GexAnalysis, analyze_gex
from .models import Bars, GexGrid, OptionChain, Replay, Symbol, SymbolIdentity, mapped
from .options import (
    BarsOptions,
    ChainOptions,
    GexHistoryOptions,
    GexOptions,
    GexReferenceOptions,
    ReplayOptions,
)
from .periods import BarPeriod, resolve_bar_window, validate_bar_window

T = TypeVar("T")


class AsyncITMClient(_TransportClient):
    """Async HTTP plus public-protobuf streaming.

    Use ``async with``, or create it directly and ``await client.aclose()``
    when you are done. A client that owns its HTTP pool and is collected
    unclosed emits ``ResourceWarning``; async cleanup cannot run from a
    finalizer, so close it explicitly.
    """

    async def get_gex(
        self, symbol: str, **options: Unpack[GexOptions]
    ) -> Result[GexGrid]:
        """Get a gamma-exposure grid.

        Options: at, tz, expiries, dte, top, by_expiry, timeout. ``dte=0``
        restricts the grid to contracts expiring on the session itself; the
        applied value comes back in ``meta["dte"]``.
        """
        result = await super().gex(symbol, **options)
        result = mapped(result, GexGrid.decode)
        if result.data is not None and result.meta.get("origin") == "adhoc":
            result = replace(result, data=replace(result.data, adhoc=True))
        return result

    async def get_gex_history(
        self, symbol: str, **options: Unpack[GexHistoryOptions]
    ) -> Result[GexHistoryData]:
        """Get a whole session's GEX captures — the replay day-bundle.

        ``date`` is required. JSON only: unlike the grid this route has no
        protobuf body, and the response is large enough that ``top`` is usually
        the right call. Past sessions are ETag-stable if you are polling one.
        """
        query: dict[str, Any] = {"date": options.get("date")}
        if "from_" in options:
            query["from"] = options["from_"]
        for key in ("to", "by_expiry", "top"):
            if key in options:
                query[key] = options[key]
        return await self.request(
            f"/v2/gex/{quote(symbol.upper(), safe='')}/history",
            query=query,
            timeout=options.get("timeout"),
        )

    async def get_gex_reference(
        self, symbol: str, **options: Unpack[GexReferenceOptions]
    ) -> Result[GexReferenceData]:
        """Get the immutable open or preceding-close GEX reference book."""
        return await super().gex_reference(symbol, **options)

    async def get_gex_analysis(
        self, symbol: str, *, levels: int = 3, **options: Unpack[GexOptions]
    ) -> Result[GexAnalysis]:
        """Fetch a grid and rank visible positive/negative strike levels.

        Full-curve net GEX and zero gamma come from the server. A tier-limited
        response can still have fewer visible levels than the complete curve.
        """
        return mapped(await self.get_gex(symbol, **options),
                      lambda grid: analyze_gex(grid, levels=levels))

    async def get_option_chain(
        self, symbol: str, **options: Unpack[ChainOptions]
    ) -> Result[OptionChain]:
        """Get contract quotes. Strike bounds are integer thousandths of dollars."""
        return mapped(await super().chain(symbol, **options), OptionChain.decode)

    async def get_bars(
        self, symbol: str, **options: Unpack[BarsOptions]
    ) -> Result[Bars]:
        """Get OHLCV bars; from_ and to are required. Prices are dollar values."""
        result = mapped(await super().bars(symbol, **options), Bars.decode)
        if result.data is not None:
            result = replace(
                result,
                data=replace(
                    result.data,
                    cursor=result.data.cursor or result.meta.get("cursor"),
                    complete_through_ms=result.data.complete_through_ms
                    or result.meta.get("complete_through"),
                    prior_close=result.data.prior_close
                    if result.data.prior_close is not None
                    else result.meta.get("prior_close"),
                ),
            )
        return result

    async def get_bars_for_period(
        self,
        symbol: str,
        period: BarPeriod,
        *,
        month: str | None = None,
        now: datetime | None = None,
        timeframe: str | None = None,
        source: Literal["trade", "mid"] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        timeout: float | None = None,
    ) -> Result[Bars]:
        """Read one New York calendar period; never silently exceed range caps."""
        window = resolve_bar_window(period, now=now, month=month)
        selected = timeframe or ("1m" if period == "today" else "1d")
        validate_bar_window(window, selected)
        options: dict[str, Any] = {
            "from_": window.start.isoformat(),
            "to": window.end.isoformat(),
            "tz": window.tz,
            "timeframe": selected,
        }
        for key, value in (
            ("source", source), ("limit", limit), ("cursor", cursor),
            ("timeout", timeout),
        ):
            if value is not None:
                options[key] = value
        return await self.get_bars(symbol, **options)

    async def get_replay(
        self, symbol: str, **options: Unpack[ReplayOptions]
    ) -> Result[Replay]:
        """Replay a session (date is required) as typed ticks or dollar bars."""
        result = mapped(await super().replay(symbol, **options), Replay.decode)
        if result.data is not None and result.data.session is None:
            result = replace(
                result, data=replace(result.data, session=options.get("date"))
            )
        return result

    async def list_expirations(
        self, symbol: str, **options: Unpack[ChainOptions]
    ) -> Result[list[str]]:
        """List available ISO expiry dates for an underlying."""
        return await super().expirations(symbol, **options)

    async def list_symbols(
        self, symbol_class: Literal["equity", "index"] | None = None
    ) -> Result[tuple[Symbol, ...]]:
        """List the supported registry, optionally filtered by asset class."""
        return mapped(
            await self.reference.list(symbol_class),
            lambda rows: tuple(Symbol.decode(r) for r in rows),
        )

    async def lookup_symbol(self, query: str) -> Result[SymbolIdentity]:
        """Resolve ticker existence and identity (does not grant data access)."""
        return mapped(
            await self.request("/v2/symbols/lookup", query={"q": query}),
            SymbolIdentity.decode,
        )

    # The new namespace always returns the same models for either HTTP codec.
    gex = get_gex
    chain = get_option_chain
    bars = get_bars
    replay = get_replay
    expirations = list_expirations


class _BlockingResource:
    """Adapt grouped resources through the client's single owned event loop."""

    def __init__(self, client: ITMClient, resource: Any):
        self._client, self._resource = client, resource

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        method = getattr(self._resource, name)

        @wraps(method)
        def call(*args: Any, **kwargs: Any) -> Any:
            return self._client._call(method, *args, **kwargs)

        return call


class _Resource:
    """Build the blocking view of a grouped resource on access.

    Not stored on the client: a resource that pointed back at a client which
    pointed at it would form a cycle, and then only the cyclic collector (on
    whatever thread it happens to run) could finalize an unclosed client.
    """

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = name

    def __get__(self, client: ITMClient | None, owner: type | None = None) -> Any:
        if client is None:
            return self
        return _BlockingResource(client, getattr(client._runtime.client, self._name))


_CLOSED = "client is closed; create a new client to make more requests"
# Bounds for the shutdown that runs from a finalizer (garbage collection or
# interpreter exit), where blocking forever would hang the caller's process.
_FINALIZE_TIMEOUT = 5.0


class _Runtime:
    """The background event loop, its portal and the async client it drives.

    The loop runs on a daemon thread, so a client that is never closed cannot
    keep the interpreter alive; ``weakref.finalize`` on the owning
    ``ITMClient`` still closes the HTTP pool and stops the loop when the
    client is collected or the interpreter exits.
    """

    def __init__(self, options: dict[str, Any]) -> None:
        self.lock = RLock()
        self.closed = False
        ready: Future[BlockingPortal] = Future()

        async def serve() -> None:
            async with BlockingPortal() as portal:
                ready.set_result(portal)
                await portal.sleep_until_stopped()

        def run() -> None:
            try:
                anyio.run(serve)
            except BaseException as error:  # noqa: BLE001 - surfaced through ``ready``
                if not ready.done():
                    ready.set_exception(error)

        # 3.14 threads may inherit the caller's context; the loop must not.
        extra: dict[str, Any] = (
            {"context": contextvars.Context()} if sys.version_info >= (3, 14) else {}
        )
        self.thread = Thread(target=run, name="itmatrix-client-loop", daemon=True, **extra)
        self.thread.start()
        self.portal = ready.result()
        try:
            self.client: AsyncITMClient = self.portal.call(
                partial(AsyncITMClient, **options)
            )
        except BaseException:
            self._stop(cancel=True, timeout=None)
            raise

    def call(self, method: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        with self.lock:
            if self.closed:
                raise RuntimeError(_CLOSED)
            return self.portal.call(partial(method, *args, **kwargs))

    def close(self, timeout: float | None = None) -> None:
        """Close the HTTP pool and stop the loop. Safe to call more than once."""
        if get_ident() == self.thread.ident:
            # Reached from the loop thread itself (a finalizer run by code on
            # the loop): it cannot wait for its own loop, so hand off.
            Thread(target=self.close, args=(_FINALIZE_TIMEOUT,), daemon=True).start()
            return
        with self.lock:
            if self.closed:
                return
            self.closed = True
            try:
                self.portal.start_task_soon(self.client.aclose).result(timeout)
            finally:
                self._stop(cancel=timeout is not None, timeout=timeout)

    def finalize(self) -> None:
        # A finalizer must never raise or hang; a leaked pool beats a hang.
        with contextlib.suppress(Exception):
            self.close(timeout=_FINALIZE_TIMEOUT)

    def _stop(self, *, cancel: bool, timeout: float | None) -> None:
        # RuntimeError: the portal already stopped. Timeout: a finalizer's bound.
        with contextlib.suppress(RuntimeError, FutureTimeout):
            self.portal.start_task_soon(self.portal.stop, cancel).result(timeout)
        self.thread.join(timeout)


class ITMClient:
    """Blocking client with one persistent HTTP pool and owned background loop.

    Create it, call it, and optionally ``close()`` it; ``with`` is not
    required. ``close()`` releases the connection pool and loop thread at a
    moment you choose and is safe to call more than once; a request after it
    raises ``RuntimeError``. A client you never close is closed when it is
    garbage-collected or when the interpreter exits, and it never keeps the
    process alive. Sync calls are serialized; use AsyncITMClient for
    concurrent requests and subscriptions. HTTP clients injected via
    http_client are not accepted; use http_transport for testing. The
    credential callback also executes on the owned loop.
    """

    account = _Resource()
    darkpool = _Resource()
    offexchange = _Resource()
    economy = _Resource()
    flow = _Resource()
    fundamentals = _Resource()
    info = _Resource()
    journal = _Resource()
    market = _Resource()
    protocols = _Resource()
    reference = _Resource()
    screener = _Resource()
    vol = _Resource()
    watchlists = _Resource()

    def __init__(
        self,
        *,
        api_key: Credential | None = None,
        token: Credential | None = None,
        base_url: str = DEFAULT_BASE_URL,
        transport: Transport = "json",
        timeout: float = 30.0,
        retries: int = 2,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._runtime = _Runtime(
            {
                "api_key": api_key,
                "token": token,
                "base_url": base_url,
                "transport": transport,
                "timeout": timeout,
                "retries": retries,
                "http_transport": http_transport,
            }
        )
        # Holds the runtime, never the client, so it cannot keep ``self`` alive.
        self._finalizer = weakref.finalize(self, self._runtime.finalize)

    @property
    def base_url(self) -> str:
        return self._runtime.client.base_url

    @property
    def closed(self) -> bool:
        """True once ``close()`` has run (or the client was finalized)."""
        return self._runtime.closed

    def __enter__(self) -> Self:
        if self._runtime.closed:
            raise RuntimeError(_CLOSED)
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        """Release the HTTP pool and background loop now. Idempotent."""
        self._finalizer.detach()
        self._runtime.close()

    def _call(self, method: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return self._runtime.call(method, *args, **kwargs)

    def get_gex(self, symbol: str, **options: Unpack[GexOptions]) -> Result[GexGrid]:
        """Get a gamma-exposure grid. Options: at, tz, expiries, dte, top, by_expiry."""
        return self._call(self._runtime.client.get_gex, symbol, **options)

    def get_gex_history(
        self, symbol: str, **options: Unpack[GexHistoryOptions]
    ) -> Result[GexHistoryData]:
        """Get a whole session's GEX captures; date is required, top is advised."""
        return self._call(self._runtime.client.get_gex_history, symbol, **options)

    def get_gex_reference(
        self, symbol: str, **options: Unpack[GexReferenceOptions]
    ) -> Result[GexReferenceData]:
        """Get the immutable open or preceding-close GEX reference book."""
        return self._call(self._runtime.client.get_gex_reference, symbol, **options)

    def get_gex_analysis(
        self, symbol: str, *, levels: int = 3, **options: Unpack[GexOptions]
    ) -> Result[GexAnalysis]:
        return self._call(self._runtime.client.get_gex_analysis, symbol, levels=levels, **options)

    def get_option_chain(
        self, symbol: str, **options: Unpack[ChainOptions]
    ) -> Result[OptionChain]:
        """Get option quotes. Strike bounds use thousandths of dollars."""
        return self._call(self._runtime.client.get_option_chain, symbol, **options)

    def get_bars(self, symbol: str, **options: Unpack[BarsOptions]) -> Result[Bars]:
        """Get OHLCV bars; supply from_ and to, optionally timeframe and cursor."""
        return self._call(self._runtime.client.get_bars, symbol, **options)

    def get_bars_for_period(
        self,
        symbol: str,
        period: BarPeriod,
        *,
        month: str | None = None,
        now: datetime | None = None,
        timeframe: str | None = None,
        source: Literal["trade", "mid"] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        timeout: float | None = None,
    ) -> Result[Bars]:
        return self._call(
            self._runtime.client.get_bars_for_period, symbol, period, month=month,
            now=now, timeframe=timeframe, source=source, limit=limit,
            cursor=cursor, timeout=timeout,
        )

    def list_expirations(
        self, symbol: str, **options: Unpack[ChainOptions]
    ) -> Result[list[str]]:
        return self._call(self._runtime.client.list_expirations, symbol, **options)

    def list_symbols(
        self, symbol_class: Literal["equity", "index"] | None = None
    ) -> Result[tuple[Symbol, ...]]:
        return self._call(self._runtime.client.list_symbols, symbol_class)

    def lookup_symbol(self, query: str) -> Result[SymbolIdentity]:
        return self._call(self._runtime.client.lookup_symbol, query)

    def request(self, path: str, **options: Any) -> Result[Any]:
        """Make an authenticated request to a relative /v2/ path."""
        return self._call(self._runtime.client.request, path, **options)

    def get_replay(
        self, symbol: str, **options: Unpack[ReplayOptions]
    ) -> Result[Replay]:
        """Replay a session (date is required) as typed ticks or dollar bars."""
        return self._call(self._runtime.client.get_replay, symbol, **options)

    gex = get_gex
    chain = get_option_chain
    bars = get_bars
    replay = get_replay
    expirations = list_expirations


# Compatibility names from the initial SDK preview.
Client = ITMClient
AsyncClient = AsyncITMClient
