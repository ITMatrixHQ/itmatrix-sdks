"""Strike filters for a live GEX book, built from keyword arguments.

``nearest=n``, ``within=fraction`` and ``between=(lo, hi)`` pick a window of the
book's strikes (at most one of them). ``top=n`` then keeps the ``n`` largest by
``by`` (``"gross"``, ``"net"``, ``"call"`` or ``"put"``). Filters choose among the
book's members only: the book carries the 120 strikes with the largest gross GEX,
so ``nearest=20`` can have gaps in the strike ladder.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import replace
from typing import Any, Literal

from .gex_book import GexBookState

TopBy = Literal["gross", "net", "call", "put"]
BookFilter = Callable[[GexBookState], GexBookState]

_RANKS: dict[str, Callable[[GexBookState, int], float]] = {
    "gross": lambda s, i: abs(s.call_gex[i]) + abs(s.put_gex[i]),
    "net": lambda s, i: abs(s.gex[i]),
    "call": lambda s, i: abs(s.call_gex[i]),
    "put": lambda s, i: abs(s.put_gex[i]),
}
_OPTIONS = frozenset({"nearest", "within", "between", "top", "by"})


def build(options: dict[str, Any]) -> tuple[BookFilter | None, dict[str, Any]]:
    """Validate filter keyword arguments; return the filter and the normalized options.

    Raises ``TypeError`` or ``ValueError`` with the argument's name on bad input.
    """
    unknown = set(options) - _OPTIONS
    if unknown:
        raise TypeError(
            f"unknown filter option {min(unknown)!r}; "
            "use nearest, within, between, top and by"
        )
    window = [name for name in ("nearest", "within", "between") if options.get(name) is not None]
    if len(window) > 1:
        raise ValueError(f"pass at most one of nearest, within and between, not {' and '.join(window)}")
    by = options.get("by")
    if by is not None and options.get("top") is None:
        raise ValueError("by applies to top: pass top=n with it")
    steps: list[Callable[[GexBookState], Iterable[int]]] = []
    normal: dict[str, Any] = {}
    if options.get("nearest") is not None:
        normal["nearest"] = _count("nearest", options["nearest"])
        steps.append(_nearest(normal["nearest"]))
    if options.get("within") is not None:
        normal["within"] = _fraction(options["within"])
        steps.append(_within(normal["within"]))
    if options.get("between") is not None:
        normal["between"] = _range(options["between"])
        steps.append(_between(*normal["between"]))
    if options.get("top") is not None:
        normal["top"] = _count("top", options["top"])
        normal["by"] = by if by is not None else "gross"
        if normal["by"] not in _RANKS:
            raise ValueError(f"by must be one of {', '.join(_RANKS)}, not {normal['by']!r}")
        steps.append(_top(normal["top"], _RANKS[normal["by"]]))
    if not steps:
        return None, normal

    def apply(state: GexBookState) -> GexBookState:
        for select in steps:
            state = _view(state, select(state))
        return state

    return apply, normal


def _nearest(count: int) -> Callable[[GexBookState], list[int]]:
    def select(state: GexBookState) -> list[int]:
        spot = state.spot
        ranked = sorted(range(len(state.strikes)), key=lambda i: (abs(state.strikes[i] - spot), i))
        return ranked[:count]

    return select


def _within(fraction: float) -> Callable[[GexBookState], list[int]]:
    def select(state: GexBookState) -> list[int]:
        spot = state.spot
        if not spot > 0:
            return []
        return [i for i, strike in enumerate(state.strikes) if abs(strike / spot - 1) <= fraction]

    return select


def _between(lo: float, hi: float) -> Callable[[GexBookState], list[int]]:
    def select(state: GexBookState) -> list[int]:
        return [i for i, strike in enumerate(state.strikes) if lo <= strike <= hi]

    return select


def _top(count: int, rank: Callable[[GexBookState, int], float]) -> Callable[[GexBookState], list[int]]:
    def select(state: GexBookState) -> list[int]:
        return sorted(range(len(state.strikes)), key=lambda i: (-rank(state, i), i))[:count]

    return select


def _count(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return value


def _fraction(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("within must be a number, a fraction of spot such as 0.05")
    if not math.isfinite(value) or value < 0:
        raise ValueError("within must be a finite fraction of spot, 0 or more")
    return float(value)


def _range(value: Any) -> tuple[float, float]:
    try:
        lo, hi = value
    except (TypeError, ValueError):
        raise TypeError("between must be a pair of strikes, such as (590, 610)") from None
    for name, bound in (("between low", lo), ("between high", hi)):
        if isinstance(bound, bool) or not isinstance(bound, int | float):
            raise TypeError(f"{name} must be a number")
        if math.isnan(bound):
            raise ValueError(f"{name} must not be NaN")
    if lo > hi:
        raise ValueError("between low must not exceed between high")
    return float(lo), float(hi)


def _view(state: GexBookState, selected: Iterable[int]) -> GexBookState:
    keep = sorted(set(selected))
    if len(keep) == len(state.strikes_thousandths):
        return state
    position = {index: at for at, index in enumerate(keep)}
    return replace(
        state,
        strikes=tuple(state.strikes[i] for i in keep),
        strikes_thousandths=tuple(state.strikes_thousandths[i] for i in keep),
        call_gex=tuple(state.call_gex[i] for i in keep),
        put_gex=tuple(state.put_gex[i] for i in keep),
        gex=tuple(state.gex[i] for i in keep),
        changed=tuple(position[i] for i in state.changed if i in position),
    )
