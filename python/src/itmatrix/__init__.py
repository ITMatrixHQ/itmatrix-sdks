"""The ITMatrixHQ API, for people and agents."""

from ._core.config import DEFAULT_BASE_URL
from ._core.errors import ITMError
from ._core.models import Transport
from ._core.stream import Stream, Subscription
from .analytics import GexAnalysis, GexLevel, analyze_gex
from .client import AsyncClient, AsyncITMClient, Client, ITMClient
from .models import (
    Bar,
    Bars,
    GexGrid,
    GexStrike,
    OptionChain,
    OptionContract,
    OptionQuote,
    Replay,
    Result,
    Symbol,
    SymbolIdentity,
    Tick,
)
from .periods import BarPeriod, BarWindow, resolve_bar_window

__all__ = [
    "DEFAULT_BASE_URL",
    "AsyncClient",
    "AsyncITMClient",
    "Bar",
    "BarPeriod",
    "BarWindow",
    "Bars",
    "Client",
    "GexAnalysis",
    "GexGrid",
    "GexLevel",
    "GexStrike",
    "ITMClient",
    "ITMError",
    "OptionChain",
    "OptionContract",
    "OptionQuote",
    "Replay",
    "Result",
    "Stream",
    "Subscription",
    "Symbol",
    "SymbolIdentity",
    "Tick",
    "Transport",
    "analyze_gex",
    "resolve_bar_window",
]
