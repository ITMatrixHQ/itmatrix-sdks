"""Filters over a live GEX book.

Each function here returns a filter: a pure function that takes a
:class:`~itmatrix.GexBookState` and returns a view of it holding a subset of the
strikes, ascending, with the same header (symbol, book_id, version, captured_at,
spot, net_gex, flip_point, reason). ``changed`` in the view lists positions
within the view. ``net_gex`` stays the whole-chain total.

Filters select among the book's members only. The book carries the top strikes
by gross GEX (``|call| + |put|``), so a near-spot strike outside that set is not
in the book and ``nearest(20)`` can have gaps in the strike ladder.

    from itmatrix.filters import nearest, top

    async with client.stream.gex_book("SPY", filter=nearest(20)) as book:
        async for view in book:
            ...
            book.set_filter(top(10, by="net"))

A filter can also be applied by hand: ``nearest(20)(state)``.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import replace
from typing import Literal

from ._core.gex_book import GexBookFilter, GexBookState

__all__ = [
    "GexBookFilter",
    "TopBy",
    "all",
    "between",
    "nearest",
    "top",
    "within_pct",
]

TopBy = Literal["gross", "net", "call", "put"]

_RANKS: dict[str, Callable[[GexBookState, int], float]] = {
    "gross": lambda s, i: abs(s.call_gex[i]) + abs(s.put_gex[i]),
    "net": lambda s, i: abs(s.gex[i]),
    "call": lambda s, i: abs(s.call_gex[i]),
    "put": lambda s, i: abs(s.put_gex[i]),
}


class _Filter:
    """A named filter, so it reads well in a repr and compares by its arguments."""

    __slots__ = ("_name", "_select")

    def __init__(
        self, name: str, select: Callable[[GexBookState], Iterable[int]] | None
    ) -> None:
        self._name = name
        self._select = select

    def __call__(self, state: GexBookState) -> GexBookState:
        if self._select is None:
            return state
        return _view(state, self._select(state))

    def __repr__(self) -> str:
        return self._name

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Filter) and other._name == self._name

    def __hash__(self) -> int:
        return hash(self._name)


def all() -> GexBookFilter:
    """Every strike in the book."""
    return _Filter("all()", None)


def nearest(n: int) -> GexBookFilter:
    """The ``n`` book strikes closest to spot. Equal distances go to the lower strike."""
    count = _count("n", n)

    def select(state: GexBookState) -> list[int]:
        spot = state.spot
        ranked = sorted(
            range(len(state.strikes)), key=lambda i: (abs(state.strikes[i] - spot), i)
        )
        return ranked[:count]

    return _Filter(f"nearest({count})", select)


def within_pct(p: float) -> GexBookFilter:
    """Book strikes within a fraction ``p`` of spot: ``|strike / spot - 1| <= p``.

    ``p`` is a fraction, so ``within_pct(0.02)`` keeps strikes within 2% of spot.
    A book without a positive spot yields no strikes.
    """
    if isinstance(p, bool) or not isinstance(p, int | float):
        raise TypeError("p must be a number")
    if not math.isfinite(p):
        raise ValueError("p must be finite")
    if p < 0:
        raise ValueError("p must not be negative")
    fraction = float(p)

    def select(state: GexBookState) -> list[int]:
        spot = state.spot
        if not spot > 0:
            return []
        return [
            i
            for i, strike in enumerate(state.strikes)
            if abs(strike / spot - 1) <= fraction
        ]

    return _Filter(f"within_pct({fraction!r})", select)


def between(lo: float, hi: float) -> GexBookFilter:
    """Book strikes from ``lo`` to ``hi`` dollars, both inclusive."""
    for name, value in (("lo", lo), ("hi", hi)):
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise TypeError(f"{name} must be a number")
        if math.isnan(value):
            raise ValueError(f"{name} must not be NaN")
    if lo > hi:
        raise ValueError("lo must not exceed hi")

    def select(state: GexBookState) -> list[int]:
        return [i for i, strike in enumerate(state.strikes) if lo <= strike <= hi]

    return _Filter(f"between({lo!r}, {hi!r})", select)


def top(n: int, by: TopBy = "gross") -> GexBookFilter:
    """The ``n`` book strikes with the largest GEX magnitude.

    ``by`` picks the measure: ``"gross"`` is ``|call| + |put|`` (how the server
    picks the book's members), ``"net"`` is ``|call + put|``, ``"call"`` and
    ``"put"`` one side. Ties go to the lower strike. The view is in strike order.
    """
    count = _count("n", n)
    rank = _RANKS.get(by) if isinstance(by, str) else None
    if rank is None:
        raise ValueError(f"by must be one of {', '.join(_RANKS)}, not {by!r}")

    def select(state: GexBookState) -> list[int]:
        ranked = sorted(range(len(state.strikes)), key=lambda i: (-rank(state, i), i))
        return ranked[:count]

    return _Filter(f"top({count}, by={by!r})", select)


def _count(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return value


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
