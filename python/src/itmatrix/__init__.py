"""The ITMatrixHQ API, for people and agents."""

from . import filters
from ._core.config import DEFAULT_BASE_URL
from ._core.errors import ITMError
from ._core.gex_book import GexBookFilter, GexBookState
from ._core.models import Transport
from ._core.stream import GexBookSubscription, Stream, StreamError, Subscription
from ._core.version import ITMUpgradeWarning
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
    "GexBookFilter",
    "GexBookState",
    "GexBookSubscription",
    "GexGrid",
    "GexLevel",
    "GexStrike",
    "ITMClient",
    "ITMError",
    "ITMUpgradeWarning",
    "OptionChain",
    "OptionContract",
    "OptionQuote",
    "Replay",
    "Result",
    "Stream",
    "StreamError",
    "Subscription",
    "Symbol",
    "SymbolIdentity",
    "Tick",
    "Transport",
    "analyze_gex",
    "filters",
    "resolve_bar_window",
]
