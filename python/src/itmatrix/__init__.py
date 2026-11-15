"""The ITMatrixHQ API, for people and agents."""

from ._core.config import DEFAULT_BASE_URL
from ._core.errors import ITMError
from ._core.live import Book, BookMeta, GexRow, LiveGex, SyncLiveGex
from ._core.models import Transport
from ._core.stream import Stream, StreamError
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
    Result,
    Symbol,
    SymbolIdentity,
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
    "Book",
    "BookMeta",
    "Client",
    "GexAnalysis",
    "GexGrid",
    "GexLevel",
    "GexRow",
    "GexStrike",
    "ITMClient",
    "ITMError",
    "ITMUpgradeWarning",
    "LiveGex",
    "OptionChain",
    "OptionContract",
    "OptionQuote",
    "Result",
    "Stream",
    "StreamError",
    "Symbol",
    "SymbolIdentity",
    "SyncLiveGex",
    "Transport",
    "analyze_gex",
    "resolve_bar_window",
]
