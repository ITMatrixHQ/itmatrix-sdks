"""Small, hand-owned public types."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Generic, Literal, TypeVar

from typing_extensions import NotRequired, TypedDict

T = TypeVar("T")
Transport = Literal["json", "protobuf"]
Credential = str | Callable[[], str | Awaitable[str]]
QueryValue = str | int | float | bool | date | datetime | Sequence[str] | None


class OffExchangeActivityRow(TypedDict):
    date: str
    off_exch_volume: float
    notional: float
    trade_count: int
    avg_size: float
    vwap: float
    off_exch_pct: float | None
    consolidated_volume: float | None
    consolidated_notional: float | None


class OffExchangeConcentrationRow(TypedDict):
    date: str
    bucket_midpoint: float
    bucket_width: float
    volume: float
    notional: float
    trade_count: int
    distinct_sessions: int
    rank_score: float
    rank: int
    bucket_count: int
    distance_bps: float | None
    lookback_sessions: int


class OffExchangeProfileRow(TypedDict):
    date: str
    price_thousandths: int
    volume: float
    notional: float
    trade_count: int


class OffExchangeCompositionRow(TypedDict):
    date: str
    category: str
    volume: float
    notional: float
    trade_count: int
    share_of_off_exch_notional: float


class PremiumBucket(TypedDict):
    session_date: str
    ts_ms: int
    call_premium_usd: float
    put_premium_usd: float
    call_prints: int
    put_prints: int
    call_contracts: int
    put_contracts: int
    call_ask_premium_usd: NotRequired[float | None]
    call_bid_premium_usd: NotRequired[float | None]
    put_ask_premium_usd: NotRequired[float | None]
    put_bid_premium_usd: NotRequired[float | None]


class PremiumData(TypedDict):
    symbol: str
    bucket_ms: int
    sessions: list[str]
    buckets: list[PremiumBucket]
    coverage_status: str


class GexHistoryStrike(TypedDict):
    """One history row. GEX values are dollars of dealer hedging per $1 move;
    each ``*_shares`` field is its dollar sibling in shares of the underlying,
    ``None`` when unknown."""

    strike: int
    gex: float
    gex_shares: float | None
    gex_0dte: NotRequired[float | None]
    gex_0dte_shares: NotRequired[float | None]
    call_oi: NotRequired[int | None]
    put_oi: NotRequired[int | None]
    delta_adj: float | None
    delta_adj_shares: float | None
    expiry: NotRequired[str | None]


class GexHistoryCapture(TypedDict):
    captured_at: int
    spot: float | None
    prior_close_spot: float | None
    net_gex: float
    net_gex_shares: float | None
    flip_point: float | None
    strikes: list[GexHistoryStrike]


class GexHistoryData(TypedDict):
    symbol: str
    session_date: str
    captures: list[GexHistoryCapture]


class GexReferenceStrike(TypedDict):
    """One reference row; ``gex_shares`` is ``gex`` divided by the book's spot."""

    strike: int
    gex: float
    gex_shares: float
    gex_0dte: NotRequired[float | None]
    gex_0dte_shares: NotRequired[float | None]
    call_oi: NotRequired[int | None]
    put_oi: NotRequired[int | None]
    delta_adj: NotRequired[float | None]
    delta_adj_shares: NotRequired[float | None]
    expiry: NotRequired[str | None]


class GexReferenceData(TypedDict):
    symbol: str
    basis: Literal["open", "prev_close"]
    session_date: str
    reference_session_date: str
    captured_at: int | None
    spot: float | None
    net_gex: float | None
    complete: bool | None
    contract_count: int | None
    gamma_absent: int | None
    strikes: list[GexReferenceStrike]


class Quote(TypedDict):
    symbol: str
    spot: float | None
    change_pct: float | None
    net_gex: float | None
    gex_change: float | None
    volume: int | None
    official_close: float | None
    session_date: str | None
    event_ts_ms: int | None


SymbolIdentity = TypedDict(
    "SymbolIdentity",
    {
        "valid": bool,
        "symbol": str,
        "instrument_type": str,
        "name": NotRequired[str | None],
        "class": NotRequired[str | None],
        "instrument_type_source": NotRequired[str | None],
        "instrument_type_as_of": NotRequired[str | None],
    },
)


@dataclass(frozen=True, slots=True)
class Result(Generic[T]):
    data: T
    meta: Mapping[str, Any]
    status: int
    request_id: str | None = None
    etag: str | None = None
    not_modified: bool = False
