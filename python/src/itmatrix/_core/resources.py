"""Grouped handwritten resources for every non-internal /v2 operation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime
from typing import Any, Literal
from urllib.parse import quote

from ._wire.rest_pb2 import (
    OffExchangeActivityResponse,
    OffExchangeCompositionResponse,
    OffExchangeConcentrationResponse,
    OffExchangeProfileResponse,
)
from .models import (
    DarkPoolLevelRow,
    OffExchangeActivityRow,
    OffExchangeCompositionRow,
    OffExchangeConcentrationRow,
    OffExchangeProfileRow,
    QueryValue,
    Quote,
    Result,
    SymbolIdentity,
)

Requester = Callable[..., Awaitable[Result[Any]]]
Classification = Literal["professional", "non_professional"]
FundamentalsDataset = Literal[
    "ratios",
    "income",
    "balance-sheet",
    "float",
    "short-interest",
    "dividends",
]
ImportFormat = Literal["jsonl", "csv"]


def _id(value: str) -> str:
    return quote(value, safe="")


def _symbol(value: str) -> str:
    return _id(value.upper())


class AccountResource:
    def __init__(self, request: Requester) -> None:
        self._request = request

    async def classification(self) -> Result[Any]:
        return await self._request("/v2/account/classification")

    async def submit_classification(
        self, classification: Classification
    ) -> Result[Any]:
        return await self._request(
            "/v2/account/classification",
            method="POST",
            body={"classification": classification},
        )

    async def usage(self) -> Result[Any]:
        return await self._request("/v2/account/usage")


class OffExchangeResource:
    """Typed synthetic off-exchange analytics.

    Every response carries freshness in ``meta`` (``session``,
    ``latest_session``, ``as_of``, ``history_sessions``) and, when the result
    was truncated to a page, an opaque ``meta.cursor`` to pass back as
    ``cursor``. Empty ``data`` with a ``meta.note`` is an honest empty answer,
    never a denial.

    Individual prints, blocks, intraday cells and evidence snapshots are
    app-only and are not representable through this resource. When the client
    uses ``transport="protobuf"``, the synthetic analytics negotiate and decode
    the endpoint-specific public protobuf message automatically; ``levels`` is
    JSON on either transport.
    """

    def __init__(self, request: Requester) -> None:
        self._request = request

    async def _get(
        self, symbol: str, view: str, message_type: type, **options: QueryValue
    ) -> Result[Any]:
        return await self._request(
            f"/v2/offexchange/{_symbol(symbol)}/{view}",
            query=options,
            message_type=message_type,
        )

    async def activity(
        self, symbol: str, **options: QueryValue
    ) -> Result[list[OffExchangeActivityRow] | OffExchangeActivityResponse]:
        return await self._get(
            symbol, "activity", OffExchangeActivityResponse, **options
        )

    async def concentration(
        self, symbol: str, **options: QueryValue
    ) -> Result[list[OffExchangeConcentrationRow] | OffExchangeConcentrationResponse]:
        return await self._get(
            symbol, "concentration", OffExchangeConcentrationResponse, **options
        )

    async def profile(
        self, symbol: str, **options: QueryValue
    ) -> Result[list[OffExchangeProfileRow] | OffExchangeProfileResponse]:
        return await self._get(symbol, "profile", OffExchangeProfileResponse, **options)

    async def composition(
        self, symbol: str, **options: QueryValue
    ) -> Result[list[OffExchangeCompositionRow] | OffExchangeCompositionResponse]:
        return await self._get(
            symbol, "composition", OffExchangeCompositionResponse, **options
        )

    async def levels(
        self,
        symbol: str,
        *,
        from_: str | int | date | datetime | None = None,
        to: str | int | date | datetime | None = None,
        tz: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        timeout: float | None = None,
    ) -> Result[list[DarkPoolLevelRow]]:
        """Off-exchange volume by price level, one row per session and level.

        ``GET /v2/darkpool/{symbol}/levels``: the one dark-pool dataset an API
        key reads. Rows ascend by session, then price. Without ``from_`` and
        ``to`` the latest session is returned; ``from_``/``to`` take epoch ms,
        ``YYYY-MM-DD`` or ``YYYY-MM-DDTHH:MM[:SS]`` in ``tz`` (default
        ``America/New_York``). ``limit`` is rows per page (default 500, at most
        5,000); pass ``meta["cursor"]`` back as ``cursor`` for the next page.
        End of day: a session appears the morning after it trades.
        """
        return await self._request(
            f"/v2/darkpool/{_symbol(symbol)}/levels",
            query={
                "from": from_,
                "to": to,
                "tz": tz,
                "limit": limit,
                "cursor": cursor,
            },
            timeout=timeout,
        )


# Compatibility name; it is the same safe resource and cannot call legacy
# /v2/darkpool evidence routes.
DarkpoolResource = OffExchangeResource


class EconomyResource:
    def __init__(self, request: Requester) -> None:
        self._request = request

    async def rates(self, **options: QueryValue) -> Result[Any]:
        return await self._request("/v2/economy/rates", query=options)

    async def treasury_yields(self) -> Result[Any]:
        return await self._request("/v2/economy/treasury-yields")


class FlowResource:
    def __init__(self, request: Requester) -> None:
        self._request = request

    async def large_trades(
        self,
        *,
        symbol: str | None = None,
        min_premium_usd: float | None = None,
        session: str | None = None,
        limit: int | None = None,
    ) -> Result[Any]:
        """Newest grouped large trades, bounded to at most 100 events.

        API requests must narrow the feed by ``symbol`` or
        ``min_premium_usd``. The SDK checks this before making a request.
        """
        if symbol is None and min_premium_usd is None:
            raise ValueError("large_trades requires symbol or min_premium_usd")
        return await self._request(
            "/v2/flow/large-trades",
            query={
                "symbol": symbol.upper() if symbol is not None else None,
                "min_premium_usd": min_premium_usd,
                "session": session,
                "limit": limit,
            },
        )

    async def cross_section(
        self, symbol: str, *, session: str | None = None
    ) -> Result[Any]:
        return await self._request(
            f"/v2/flow/{_symbol(symbol)}/cross-section",
            query={"session": session},
        )

    async def eod(
        self,
        *,
        session: str,
        symbol: str | None = None,
        min_premium_usd: float | None = None,
        from_ms: int | None = None,
        to_ms: int | None = None,
        right: Literal["call", "put"] | None = None,
        expiry: str | None = None,
    ) -> Result[Any]:
        """Persisted large prints for one explicit completed session.

        API requests must also narrow by ``symbol`` or
        ``min_premium_usd``. The server returns at most 100 ranked prints.
        """
        if symbol is None and min_premium_usd is None:
            raise ValueError("eod requires symbol or min_premium_usd")
        return await self._request(
            "/v2/flow/eod",
            query={
                "session": session,
                "symbol": symbol.upper() if symbol is not None else None,
                "min_premium_usd": min_premium_usd,
                "from_ms": from_ms,
                "to_ms": to_ms,
                "right": right,
                "expiry": expiry,
            },
        )

class VolResource:
    """Our own implied-vol surface, out of the greeks engine (``site.vol``).

    These are our numbers rather than a vendor's, which is why they are ours
    to serve at all.
    """

    def __init__(self, request: Requester) -> None:
        self._request = request

    async def term(self, symbol: str) -> Result[Any]:
        """ATM term structure for an underlying."""
        return await self._request(f"/v2/options/{_symbol(symbol)}/vol/term")

    async def surface(self, symbol: str) -> Result[Any]:
        """The vol surface for an underlying."""
        return await self._request(f"/v2/options/{_symbol(symbol)}/vol/surface")

    async def greeks(self, contract: str, *, session: str | None = None) -> Result[Any]:
        """Per-contract greeks. ``contract`` is an OCC symbol."""
        return await self._request(
            f"/v2/options/{_symbol(contract)}/greeks", query={"session": session}
        )


class FundamentalsResource:
    def __init__(self, request: Requester) -> None:
        self._request = request

    async def get(
        self,
        symbol: str,
        dataset: FundamentalsDataset,
        *,
        timeframe: Literal["quarterly", "annual", "trailing_twelve_months"]
        | None = None,
        limit: int | None = None,
        vintage: Literal["latest", "as_reported"] | None = None,
        as_of: str | date | datetime | None = None,
        if_none_match: str | None = None,
        timeout: float | None = None,
    ) -> Result[Any]:
        """One fundamentals dataset.

        ``timeframe`` picks the statement period and ``limit`` the row count
        (newest first, 1..=50) for the statement datasets; the backend ignores
        both for the others. ``vintage`` applies to ``income`` and
        ``balance-sheet``: ``"latest"`` (the default) is the newest, restated
        values; ``"as_reported"`` is what had been observed at ``as_of`` (an
        RFC 3339 instant, or a ``YYYY-MM-DD`` date meaning the end of that UTC
        day), the only view a backtest or screen should use. ``as_of`` without
        ``vintage="as_reported"`` is refused before any request.
        ``if_none_match`` revalidates a cached read — the 304 comes back as
        ``Result(not_modified=True)``, which is cache confirmation, not an
        empty dataset.
        """
        if as_of is not None and vintage != "as_reported":
            raise ValueError('as_of needs vintage="as_reported"')
        return await self._request(
            f"/v2/fundamentals/{_symbol(symbol)}/{_id(dataset)}",
            query={
                "timeframe": timeframe,
                "limit": limit,
                "vintage": vintage,
                "as_of": as_of,
            },
            if_none_match=if_none_match,
            timeout=timeout,
        )

class JournalResource:
    def __init__(self, request: Requester) -> None:
        self._request = request

    async def list_accounts(self) -> Result[Any]:
        return await self._request("/v2/journal/accounts")

    async def create_account(self, account: Mapping[str, Any]) -> Result[Any]:
        return await self._request("/v2/journal/accounts", method="POST", body=account)

    async def put_account(
        self, account_id: str, account: Mapping[str, Any]
    ) -> Result[Any]:
        return await self._request(
            f"/v2/journal/accounts/{_id(account_id)}",
            method="PUT",
            body=account,
        )

    async def patch_account(
        self, account_id: str, patch: Mapping[str, Any]
    ) -> Result[Any]:
        return await self._request(
            f"/v2/journal/accounts/{_id(account_id)}",
            method="PATCH",
            body=patch,
        )

    async def delete_account(self, account_id: str) -> Result[Any]:
        return await self._request(
            f"/v2/journal/accounts/{_id(account_id)}", method="DELETE"
        )

    async def list_trades(self) -> Result[Any]:
        return await self._request("/v2/journal/trades")

    async def create_trade(self, trade: Mapping[str, Any]) -> Result[Any]:
        return await self._request("/v2/journal/trades", method="POST", body=trade)

    async def put_trade(self, trade_id: str, trade: Mapping[str, Any]) -> Result[Any]:
        return await self._request(
            f"/v2/journal/trades/{_id(trade_id)}",
            method="PUT",
            body=trade,
        )

    async def patch_trade(self, trade_id: str, patch: Mapping[str, Any]) -> Result[Any]:
        return await self._request(
            f"/v2/journal/trades/{_id(trade_id)}",
            method="PATCH",
            body=patch,
        )

    async def delete_trade(self, trade_id: str) -> Result[Any]:
        return await self._request(
            f"/v2/journal/trades/{_id(trade_id)}", method="DELETE"
        )

    async def import_trades(
        self,
        contents: str,
        *,
        import_id: str,
        format: ImportFormat = "jsonl",
    ) -> Result[Any]:
        return await self._request(
            "/v2/journal/import",
            method="POST",
            query={"import_id": import_id, "format": format},
            body=contents,
            content_type="text/plain",
        )


class MarketResource:
    def __init__(self, request: Requester) -> None:
        self._request = request

    async def quotes(
        self, symbols: str | list[str] | tuple[str, ...]
    ) -> Result[list[Quote]]:
        return await self._request("/v2/quotes", query={"symbols": symbols})

    async def spot(self, symbol: str) -> Result[Any]:
        return await self._request(f"/v2/symbols/{_symbol(symbol)}/spot")

    async def option_bars(self, contract: str, **options: QueryValue) -> Result[Any]:
        """OHLCV bars for a single option contract (OCC symbol).

        The per-contract counterpart to the client's ``get_bars``, which is
        underlying-scoped.
        """
        return await self._request(
            f"/v2/options/{_symbol(contract)}/bars", query=dict(options)
        )


class ReferenceResource:
    def __init__(self, request: Requester) -> None:
        self._request = request

    async def list(
        self, symbol_class: Literal["equity", "index"] | None = None
    ) -> Result[Any]:
        return await self._request("/v2/symbols", query={"class": symbol_class})

    async def get(self, symbol: str) -> Result[Any]:
        return await self._request(f"/v2/symbols/{_symbol(symbol)}")

    async def lookup(self, query: str) -> Result[SymbolIdentity]:
        return await self._request("/v2/symbols/lookup", query={"q": query})


class ScreenerResource:
    def __init__(self, request: Requester) -> None:
        self._request = request

    async def movers(self, **options: QueryValue) -> Result[Any]:
        return await self._request("/v2/screener/movers", query=options)

    async def sectors(self, *, date: str | None = None) -> Result[Any]:
        return await self._request("/v2/screener/sectors", query={"date": date})


class ProtocolResource:
    def __init__(self, request: Requester) -> None:
        self._request = request

    async def websocket_schema(self) -> Result[str]:
        return await self._request("/v2/ws-protocol.proto", accept="text/plain")

    async def rest_schema(self) -> Result[str]:
        return await self._request("/v2/rest-protocol.proto", accept="text/plain")


class InfoResource:
    """Service discovery: liveness, readiness and the spec itself.

    ``/healthz`` and ``/readyz`` are the only paths this SDK requests outside
    ``/v2/`` (see ``_ROOT_PATHS`` in client.py, which allows exactly those two).
    The API declares them ``public: true``, skipping the auth extractor, so
    they answer before a key exists — which is the point of them.

    ``ready()`` is the deploy contract, not a synonym for ``health()``: it
    reports warm-up and is non-2xx while the process is still filling caches.
    """

    def __init__(self, request: Requester) -> None:
        self._request = request

    async def health(self) -> Result[Any]:
        """Liveness. 200 once the process is serving at all."""
        return await self._request("/healthz")

    async def ready(self) -> Result[Any]:
        """Readiness including warm-up — non-2xx means "not yet"."""
        return await self._request("/readyz")

    async def openapi(self) -> Result[Any]:
        """The live OpenAPI document.

        Its ``info.version``, operation set and ``x-exposure`` stamps are the
        authority on what this deployment actually serves.
        """
        return await self._request("/v2/openapi.json")


class WatchlistsResource:
    def __init__(self, request: Requester) -> None:
        self._request = request

    async def list(self) -> Result[Any]:
        return await self._request("/v2/watchlists")

    async def put(self, watchlist_id: str, watchlist: Mapping[str, Any]) -> Result[Any]:
        return await self._request(
            f"/v2/watchlists/{_id(watchlist_id)}",
            method="PUT",
            body=watchlist,
        )

    async def delete(self, watchlist_id: str) -> Result[Any]:
        return await self._request(
            f"/v2/watchlists/{_id(watchlist_id)}", method="DELETE"
        )


SUPPORTED_OPERATIONS = frozenset(
    {
        "account_getClassification",
        "account_submitClassification",
        "account_getUsage",
        "bars_getBars",
        "bars_getOptionBars",
        "chain_getChain",
        "chain_getExpirations",
        "offexchange_getActivity",
        "offexchange_getConcentration",
        "offexchange_getProfile",
        "offexchange_getComposition",
        "darkpool_getLevels",
        "economy_getRates",
        "flow_getCrossSection",
        "flow_getEod",
        "flow_getLargeTrades",
        "economy_getTreasuryYields",
        "fundamentals_getDataset",
        "gex_getGrid",
        "gex_getHistory",
        "gex_getReference",
        "journal_listAccounts",
        "journal_createAccount",
        "journal_putAccount",
        "journal_patchAccount",
        "journal_deleteAccount",
        "journal_importTrades",
        "journal_listTrades",
        "journal_createTrade",
        "journal_putTrade",
        "journal_patchTrade",
        "journal_deleteTrade",
        "market_getQuotes",
        "market_getSpot",
        "reference_listSymbols",
        "reference_getSymbol",
        "reference_lookupSymbol",
        "screener_getMovers",
        "screener_getSectors",
        "stream_createTicket",
        "stream_connectWs",
        "stream_getWsProtocolProto",
        "stream_getRestProtocolProto",
        "vol_getContractGreeks",
        "vol_getSurface",
        "vol_getTerm",
        "watchlists_listWatchlists",
        "watchlists_putWatchlist",
        "watchlists_deleteWatchlist",
    }
)

# Pinned public operations that have no SDK method (none at pin 2.21.0).
INTENTIONALLY_UNSUPPORTED_OPERATIONS: frozenset[str] = frozenset()
