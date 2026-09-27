"""Named options for IDE completion and agent signature inspection.

Every options type carries an optional ``timeout``: a per-call override of the
client-wide timeout, which is Python's counterpart to the TypeScript SDK's
per-call ``AbortSignal``. There is deliberately no cancellation token beyond
it — cancelling the ``asyncio`` task cancels the request, and the blocking
client serialises calls on its own loop, so a token would duplicate the
language's own mechanism.
"""

from datetime import datetime
from typing import Literal

from typing_extensions import NotRequired, TypedDict


class GexOptions(TypedDict, total=False):
    at: str | int | datetime
    tz: str
    expiries: str | list[str] | tuple[str, ...]
    #: Days-to-expiry filter. The backend implements ``0`` only, and echoes
    #: whatever it applied in ``meta["dte"]``; no echo means no filter.
    dte: int
    top: int
    by_expiry: bool
    timeout: float


class GexHistoryOptions(TypedDict):
    """Options for a whole session's GEX captures.

    ``date`` is the ET session; ``from_``/``to`` are ``HH:MM`` exchange-local
    bounds on it, half-open ``[from, to)`` like the stocks replay window, so
    ``to="16:00"`` drops the closing capture — ask for ``"16:05"`` to keep it.
    A full uncapped day measures around 5.5 MB, so ``top`` is usually wanted.
    """

    date: str
    from_: NotRequired[str]
    to: NotRequired[str]
    by_expiry: NotRequired[bool]
    top: NotRequired[int]
    timeout: NotRequired[float]


class GexReferenceOptions(TypedDict):
    """An exact exchange session and immutable reference basis."""

    date: str
    basis: Literal["open", "prev_close"]


class ChainOptions(TypedDict, total=False):
    at: str | int | datetime
    tz: str
    expiry: str
    strike_gte: int
    strike_lte: int
    timeout: float


class BarsOptions(TypedDict):
    from_: str | int | datetime
    to: str | int | datetime
    timeframe: NotRequired[str]
    tz: NotRequired[str]
    source: NotRequired[Literal["trade", "mid"]]
    limit: NotRequired[int]
    cursor: NotRequired[str]
    timeout: NotRequired[float]


class ReplayOptions(TypedDict):
    date: str
    from_: NotRequired[str]
    to: NotRequired[str]
    tz: NotRequired[str]
    timeout: NotRequired[float]
