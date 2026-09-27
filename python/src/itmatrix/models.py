"""Transport-independent values. Prices are dollars, dates are ISO dates.

Strikes retain their exact integer representation as ``strike_thousandths``;
``strike`` is a convenience dollar value. Missing measurements remain None.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, timedelta
from functools import lru_cache
from typing import Any, Literal, TypeVar

from google.protobuf.message import Message

from ._core.models import Result

T = TypeVar("T")


def mapped(result: Result[Any], decode: Callable[[Any], T]) -> Result[T]:
    return replace(
        result, data=decode(result.data) if not result.not_modified else None
    )


def _optional(message: Any, field: str) -> Any:
    return getattr(message, field) if message.HasField(field) else None


@lru_cache(maxsize=512)
def _day(day: int | None) -> str | None:
    return (
        (date(1970, 1, 1) + timedelta(days=day)).isoformat()
        if day is not None
        else None
    )


@dataclass(frozen=True, slots=True)
class GexStrike:
    """One grid row. GEX values are dollars of dealer hedging per $1 move;
    each ``*_shares`` field is its dollar sibling in shares of the underlying,
    ``None`` when the server does not know it."""

    strike_thousandths: int
    gex: float
    gex_0dte: float | None = None
    call_oi: int | None = None
    put_oi: int | None = None
    delta_adj: float | None = None
    expiry: str | None = None
    gex_shares: float | None = None
    gex_0dte_shares: float | None = None
    delta_adj_shares: float | None = None

    @property
    def strike(self) -> float:
        return self.strike_thousandths / 1000


# The frozen dataclass initializer calls object.__setattr__ for every field.
# Its slot descriptors can initialize a fresh, unexposed instance directly,
# without repeated name lookup. Public construction and immutability stay intact.
(
    _set_strike_thousandths,
    _set_gex,
    _set_gex_0dte,
    _set_call_oi,
    _set_put_oi,
    _set_delta_adj,
    _set_expiry,
    _set_gex_shares,
    _set_gex_0dte_shares,
    _set_delta_adj_shares,
) = tuple(getattr(GexStrike, name).__set__ for name in GexStrike.__slots__)


def _make_gex_strike(
    strike_thousandths: int,
    gex: float,
    gex_0dte: float | None,
    call_oi: int | None,
    put_oi: int | None,
    delta_adj: float | None,
    expiry: str | None,
    gex_shares: float | None,
    gex_0dte_shares: float | None,
    delta_adj_shares: float | None,
) -> GexStrike:
    row = object.__new__(GexStrike)
    _set_strike_thousandths(row, strike_thousandths)
    _set_gex(row, gex)
    _set_gex_0dte(row, gex_0dte)
    _set_call_oi(row, call_oi)
    _set_put_oi(row, put_oi)
    _set_delta_adj(row, delta_adj)
    _set_expiry(row, expiry)
    _set_gex_shares(row, gex_shares)
    _set_gex_0dte_shares(row, gex_0dte_shares)
    _set_delta_adj_shares(row, delta_adj_shares)
    return row


@dataclass(frozen=True, slots=True)
class GexGrid:
    strikes: tuple[GexStrike, ...]
    net_gex: float
    max_abs_gex: float
    spot: float | None = None
    prior_close_spot: float | None = None
    captured_at_ms: int | None = None
    flip_point: float | None = None
    adhoc: bool = False
    net_gex_shares: float | None = None
    max_abs_gex_shares: float | None = None

    @classmethod
    def decode(cls, data: Any) -> GexGrid:
        if isinstance(data, Message):
            rows = []
            for r in data.rows:
                has = r.HasField
                rows.append(
                    _make_gex_strike(
                        r.strike_thousandths,
                        r.gex,
                        r.gex_0dte if has("gex_0dte") else None,
                        r.call_oi if has("call_oi") else None,
                        r.put_oi if has("put_oi") else None,
                        r.delta_adj if has("delta_adj") else None,
                        _day(r.expiry_epoch_day) if has("expiry_epoch_day") else None,
                        r.gex_shares if has("gex_shares") else None,
                        r.gex_0dte_shares if has("gex_0dte_shares") else None,
                        r.delta_adj_shares if has("delta_adj_shares") else None,
                    )
                )
            return cls(
                tuple(rows),
                data.net_gex,
                data.max_abs_gex,
                _optional(data, "spot"),
                _optional(data, "prior_close_spot"),
                _optional(data, "captured_at_ms"),
                _optional(data, "flip_point"),
                data.adhoc,
                _optional(data, "net_gex_shares"),
                _optional(data, "max_abs_gex_shares"),
            )
        rows = tuple(
            _make_gex_strike(
                r["strike"],
                r["gex"],
                r.get("gex_0dte"),
                r.get("call_oi"),
                r.get("put_oi"),
                r.get("delta_adj"),
                r.get("expiry"),
                r.get("gex_shares"),
                r.get("gex_0dte_shares"),
                r.get("delta_adj_shares"),
            )
            for r in data["strikes"]
        )
        return cls(
            rows,
            data["net_gex"],
            data["max_abs_gex"],
            data.get("spot"),
            data.get("prior_close_spot"),
            data.get("captured_at"),
            data.get("flip_point"),
            data.get("adhoc", False),
            data.get("net_gex_shares"),
            data.get("max_abs_gex_shares"),
        )


@dataclass(frozen=True, slots=True)
class OptionContract:
    expiry: str
    right: Literal["call", "put"]
    strike_thousandths: int
    # Public protobuf omits the OSI root; do not guess it from the request symbol.
    underlying: str | None = None

    @property
    def strike(self) -> float:
        return self.strike_thousandths / 1000


@dataclass(frozen=True, slots=True)
class OptionQuote:
    contract: OptionContract
    oi: int | None = None
    volume: int | None = None
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    fmv: float | None = None
    iv: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    shares_per_contract: int | None = None


@dataclass(frozen=True, slots=True)
class OptionChain:
    rows: tuple[OptionQuote, ...]
    spot: float | None = None
    captured_at_ms: int | None = None
    partial: bool = False

    @classmethod
    def decode(cls, data: Any) -> OptionChain:
        fields = (
            "oi",
            "volume",
            "bid",
            "ask",
            "last",
            "fmv",
            "iv",
            "delta",
            "gamma",
            "theta",
            "vega",
        )
        rows = []
        if isinstance(data, Message):
            for r in data.rows:
                if r.right not in (0, 1):
                    raise ValueError(f"unknown option right {r.right}")
                contract = OptionContract(
                    _day(r.expiry_epoch_day),
                    "call" if r.right == 0 else "put",
                    r.strike_thousandths,
                )
                rows.append(
                    OptionQuote(
                        contract,
                        **{f: _optional(r, f) for f in fields},
                        shares_per_contract=r.shares_per_contract,
                    )
                )
            return cls(
                tuple(rows),
                _optional(data, "spot"),
                _optional(data, "captured_at_ms"),
                data.partial,
            )
        for row in data["rows"]:
            c = row["contract"]
            if c["right"] not in ("call", "put"):
                raise ValueError(f"unknown option right {c['right']!r}")
            contract = OptionContract(
                c["expiry"], c["right"], c["strike"], c.get("underlying")
            )
            rows.append(
                OptionQuote(
                    contract,
                    **{f: row.get(f) for f in fields},
                    shares_per_contract=row.get("shares_per_contract"),
                )
            )
        return cls(
            tuple(rows),
            data.get("spot"),
            data.get("captured_at"),
            data.get("partial", False),
        )


@dataclass(frozen=True, slots=True)
class Bar:
    ts_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: int | None = None
    vwap: float | None = None
    trade_count: int | None = None


@dataclass(frozen=True, slots=True)
class Bars:
    bars: tuple[Bar, ...]
    cursor: str | None = None
    complete_through_ms: int | None = None
    prior_close: float | None = None

    @classmethod
    def decode(cls, data: Any) -> Bars:
        if isinstance(data, Message):
            scale = data.price_scale
            if scale <= 0:
                raise ValueError("protobuf price_scale must be positive")
            rows = tuple(
                Bar(
                    r.ts_ms,
                    r.o / scale,
                    r.h / scale,
                    r.l / scale,
                    r.c / scale,
                    _optional(r, "v"),
                    r.vwap / scale if r.HasField("vwap") else None,
                    _optional(r, "n"),
                )
                for r in data.bars
            )
            return cls(
                rows,
                data.cursor or None,
                _optional(data, "complete_through_ms"),
                data.prior_close_scaled / scale
                if data.HasField("prior_close_scaled")
                else None,
            )
        return cls(
            tuple(Bar(**{f: r.get(f) for f in Bar.__dataclass_fields__}) for r in data)
        )


@dataclass(frozen=True, slots=True)
class Symbol:
    symbol: str
    name: str
    symbol_class: str
    min_tier: str
    requires_attestation: bool
    chain_policy: dict[str, Any]

    @classmethod
    def decode(cls, data: Any) -> Symbol:
        return cls(
            data["symbol"],
            data["name"],
            data["class"],
            data["min_tier"],
            data["requires_attestation"],
            data["chain_policy"],
        )


@dataclass(frozen=True, slots=True)
class SymbolIdentity:
    valid: bool
    symbol: str
    name: str | None
    symbol_class: str | None
    instrument_type: str = "unknown"
    instrument_type_source: str | None = None
    instrument_type_as_of: str | None = None

    @classmethod
    def decode(cls, data: Any) -> SymbolIdentity:
        return cls(
            data["valid"],
            data["symbol"],
            data.get("name"),
            data.get("class"),
            data.get("instrument_type", "unknown"),
            data.get("instrument_type_source"),
            data.get("instrument_type_as_of"),
        )


@dataclass(frozen=True, slots=True)
class Tick:
    ts_ms: int
    price: float
    source: str
    seq: int


@dataclass(frozen=True, slots=True)
class Replay:
    fidelity: Literal["ticks", "bars1m"]
    complete: bool
    events: tuple[Tick | Bar, ...]
    session: str | None = None

    @classmethod
    def decode(cls, data: Any) -> Replay:
        if isinstance(data, Message):
            if data.price_scale <= 0:
                raise ValueError("protobuf price_scale must be positive")
            if data.fidelity == 0:
                events = tuple(
                    Tick(
                        t.ts_ms,
                        t.price_scaled / data.price_scale,
                        data.sources[t.source_id],
                        t.seq,
                    )
                    for t in data.ticks
                )
                return cls("ticks", data.complete, events)
            if data.fidelity != 1:
                raise ValueError(f"unknown replay fidelity {data.fidelity}")
            scale = data.price_scale
            events = tuple(
                Bar(
                    r.ts_ms,
                    r.o / scale,
                    r.h / scale,
                    r.l / scale,
                    r.c / scale,
                    _optional(r, "v"),
                    r.vwap / scale if r.HasField("vwap") else None,
                    _optional(r, "n"),
                )
                for r in data.bars
            )
            return cls("bars1m", data.complete, events)
        fidelity = data["fidelity"]
        if fidelity == "ticks":
            events = tuple(Tick(*event) for event in data["events"])
        elif fidelity == "bars1m":
            events = tuple(
                Bar(e["ts"], e["o"], e["h"], e["l"], e["c"], e.get("v"))
                for e in data["events"]
            )
        else:
            raise ValueError(f"unknown replay fidelity {fidelity!r}")
        return cls(fidelity, data["complete"], events, data.get("session"))
