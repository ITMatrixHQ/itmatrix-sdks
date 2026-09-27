"""Deterministic client-side analytics over public ITMatrixHQ models."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from .models import GexGrid


@dataclass(frozen=True, slots=True)
class GexLevel:
    """One returned strike, in dollars and exact integer thousandths."""

    strike: float
    strike_thousandths: int
    gex: float
    distance_from_spot: float | None


@dataclass(frozen=True, slots=True)
class GexAnalysis:
    """Server scalars plus ranked levels among the rows the caller received.

    ``zero_gamma`` is the backend's ``flip_point``, including honest ``None``.
    ``positive_levels`` and ``negative_levels`` rank *returned* strike rows;
    tier limits or a caller's ``top`` trim can hide stronger levels. This
    object never recomputes full-curve scalars from a partial row list.
    """

    grid: GexGrid
    regime: Literal["positive", "negative", "flat"]
    zero_gamma: float | None
    positive_levels: tuple[GexLevel, ...]
    negative_levels: tuple[GexLevel, ...]
    levels_scope: Literal["returned_rows"] = "returned_rows"

    @property
    def net_gex(self) -> float:
        return self.grid.net_gex


def analyze_gex(grid: GexGrid, *, levels: int = 3) -> GexAnalysis:
    """Summarize a grid without another request or invented market values.

    Duplicate strikes from a ``by_expiry`` grid are summed before ranking, so
    the result is per strike. Ties go to the lower strike.
    """
    if levels < 1:
        raise ValueError("levels must be at least 1")
    if not math.isfinite(grid.net_gex):
        raise ValueError("net_gex must be finite")
    by_strike: dict[int, float] = {}
    for row in grid.strikes:
        if not math.isfinite(row.gex):
            raise ValueError("strike gex must be finite")
        by_strike[row.strike_thousandths] = (
            by_strike.get(row.strike_thousandths, 0.0) + row.gex
        )

    def level(strike: int, gex: float) -> GexLevel:
        dollars = strike / 1000
        return GexLevel(
            dollars,
            strike,
            gex,
            dollars - grid.spot if grid.spot is not None else None,
        )

    positive = sorted(
        ((s, g) for s, g in by_strike.items() if g > 0),
        key=lambda item: (-item[1], item[0]),
    )[:levels]
    negative = sorted(
        ((s, g) for s, g in by_strike.items() if g < 0),
        key=lambda item: (item[1], item[0]),
    )[:levels]
    regime: Literal["positive", "negative", "flat"] = (
        "positive" if grid.net_gex > 0 else "negative" if grid.net_gex < 0 else "flat"
    )
    return GexAnalysis(
        grid,
        regime,
        grid.flip_point,
        tuple(level(s, g) for s, g in positive),
        tuple(level(s, g) for s, g in negative),
    )
