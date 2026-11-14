"""Client-side GEX book: keyframes, updates and the CRC that ties them together.

The stream sends one ``GexStrikeBook`` keyframe per subscription and then
``GexStrikeBookUpdate`` messages that carry only the strikes whose call or put
value changed. :class:`GexBookTracker` holds the book for one symbol and applies
those frames. It does no I/O, so every rule here can be tested with bytes alone.

Rules:

* A keyframe replaces all state and adopts its ``book_id`` and ``version``, after
  its CRC checks out. A keyframe that fails the CRC or is malformed is not adopted
  and asks for a resync.
* An update applies only when ``book_id`` matches and ``version`` is exactly one
  more than the current version, and only if the CRC of the book after applying
  it matches the CRC in the message.
* A ``version`` at or below the current one is a duplicate and is ignored.
* Anything else, or a CRC mismatch, leaves the book untouched and asks for a
  resync. Updates are dropped until the next keyframe arrives.
"""

from __future__ import annotations

import operator
import struct
import zlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import accumulate

from ._wire.ws_pb2 import Frame, GexBookReason, GexStrikeBook, GexStrikeBookUpdate

_REASONS = {
    GexBookReason.GEX_BOOK_REASON_SUBSCRIBE: "subscribe",
    GexBookReason.GEX_BOOK_REASON_PERIODIC: "periodic",
    GexBookReason.GEX_BOOK_REASON_RESYNC: "resync",
    GexBookReason.GEX_BOOK_REASON_REBUILD: "rebuild",
    GexBookReason.GEX_BOOK_REASON_RECOVERY: "recovery",
}
# One strike in the CRC: thousandths int64, call float32, put float32, little-endian.
# A book's CRC input is n of these 16-byte rows; the tracker keeps those bytes
# and patches only the 8 value bytes of each changed row on an update.
_ROW_SIZE = 16
_THOUSAND = 1000
_VALUES = struct.Struct("<ff")
_BOOK_ROWS: dict[int, struct.Struct] = {}


def _rows_struct(count: int) -> struct.Struct:
    rows = _BOOK_ROWS.get(count)
    if rows is None:
        rows = _BOOK_ROWS.setdefault(count, struct.Struct("<" + "qff" * count))
    return rows


def _crc_rows(
    thousandths: Sequence[int], call: Sequence[float], put: Sequence[float]
) -> bytearray:
    """The CRC input of a book: one packed row per strike."""
    count = len(thousandths)
    if len(call) != count or len(put) != count:
        raise ValueError("strikes, call_gex and put_gex differ in length")
    args: list[object] = [None] * (3 * count)
    args[0::3] = thousandths
    args[1::3] = call
    args[2::3] = put
    return bytearray(_rows_struct(count).pack(*args))

@dataclass(frozen=True, slots=True)
class GexBookState:
    """The book after one applied keyframe or update.

    ``strikes`` are dollars and ``strikes_thousandths`` the same strikes as exact
    integers, both ascending. ``call_gex`` and ``put_gex`` are dollars of dealer
    hedging per $1 move, single-precision values as sent, aligned with the
    strikes: calls are positive, puts negative. ``gex`` is the total per strike,
    ``call_gex + put_gex`` computed in double precision. ``net_gex`` is the
    whole-chain total, not the sum of the listed strikes. ``changed`` lists the
    positions the latest frame updated (every position for a keyframe), and
    ``reason`` says why a keyframe was sent (``None`` for an update).

    A filter (see :mod:`itmatrix.filters`) returns a state of the same shape
    holding a subset of the strikes; ``changed`` then indexes that subset.
    """

    symbol: str
    book_id: int
    version: int
    captured_at: datetime
    spot: float
    net_gex: float
    flip_point: float | None
    strikes: tuple[float, ...]
    strikes_thousandths: tuple[int, ...]
    call_gex: tuple[float, ...]
    put_gex: tuple[float, ...]
    gex: tuple[float, ...]
    changed: tuple[int, ...]
    reason: str | None


GexBookFilter = Callable[[GexBookState], GexBookState]
"""A pure function from a book to a view of it with the same header."""


def book_crc(
    strikes_thousandths: Iterable[int],
    call_gex: Iterable[float],
    put_gex: Iterable[float],
) -> int:
    """CRC-32 (IEEE, as zlib) of a book.

    For each strike in ascending order: the strike in thousandths as a
    little-endian int64, then the call value and then the put value as
    little-endian float32 bits.
    """
    rows = _crc_rows(tuple(strikes_thousandths), tuple(call_gex), tuple(put_gex))
    return zlib.crc32(rows) & 0xFFFFFFFF


class GexBookTracker:
    """The book for one symbol. Feed it frames; it says what to yield."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.state: GexBookState | None = None
        self.awaiting_keyframe = False
        # The CRC input of ``state``: 16 bytes per strike (see ``book_crc``).
        self._rows = bytearray()

    def reset(self) -> None:
        """Forget the book, as after a reconnect. Updates wait for a keyframe."""
        self.state = None
        self.awaiting_keyframe = False
        self._rows = bytearray()

    def apply(self, frame: Frame) -> tuple[GexBookState | None, bool]:
        """Apply one frame.

        Returns ``(state, resync)``. ``state`` is the new book when the frame
        changed it, else ``None``. ``resync`` is true when the client must ask for
        a fresh keyframe (it stays true for every update dropped while waiting).
        """
        kind = frame.WhichOneof("payload")
        if kind == "gex_strike_book_update":
            return self._update(frame.gex_strike_book_update)
        if kind == "gex_strike_book":
            return self._keyframe(frame.gex_strike_book)
        return None, False

    def _keyframe(self, book: GexStrikeBook) -> tuple[GexBookState | None, bool]:
        thousandths = tuple(accumulate(book.strikes))
        count = len(thousandths)
        call = tuple(book.call_gex)
        put = tuple(book.put_gex)
        if len(call) != count or len(put) != count:
            # A malformed keyframe cannot be adopted. Ask for another.
            return self._desync()
        if not all(map(operator.lt, thousandths, thousandths[1:])):
            return self._desync()
        try:
            rows = _crc_rows(thousandths, call, put)
        except struct.error:
            return self._desync()  # a strike outside int64
        if zlib.crc32(rows) != book.crc:
            # The keyframe is the base of every update after it: never adopt a
            # book that does not match its own checksum.
            return self._desync()
        self.awaiting_keyframe = False
        self._rows = rows
        self.state = self._state(
            book,
            book.book_id,
            thousandths,
            tuple(map(_THOUSAND.__rtruediv__, thousandths)),
            call,
            put,
            tuple(map(operator.add, call, put)),
            tuple(range(count)),
            _REASONS.get(book.reason),
        )
        return self.state, False

    def _update(self, update: GexStrikeBookUpdate) -> tuple[GexBookState | None, bool]:
        state = self.state
        if state is None or self.awaiting_keyframe:
            # Nothing to apply it to, or already resyncing: drop it.
            return None, True
        if update.book_id != state.book_id:
            return self._desync()
        version = update.version
        if version <= state.version:
            return None, False  # duplicate
        if version != state.version + 1:
            return self._desync()
        index = tuple(update.index)
        new_call = tuple(update.call_gex)
        new_put = tuple(update.put_gex)
        count = len(state.strikes_thousandths)
        if len(new_call) != len(index) or len(new_put) != len(index):
            return self._desync()
        previous = -1
        for position in index:
            if position <= previous or position >= count:
                return self._desync()
            previous = position
        call = list(state.call_gex)
        put = list(state.put_gex)
        gex = list(state.gex)
        for position, c, p in zip(index, new_call, new_put):
            call[position] = c
            put[position] = p
            gex[position] = c + p
        if len(index) * 4 > count:
            # Most rows moved: one pack of the whole book beats patching each.
            rows = _crc_rows(state.strikes_thousandths, call, put)
        else:
            rows = bytearray(self._rows)
            pack_into = _VALUES.pack_into
            for position, c, p in zip(index, new_call, new_put):
                pack_into(rows, position * _ROW_SIZE + 8, c, p)
        if zlib.crc32(rows) != update.crc:
            return self._desync()
        self._rows = rows
        self.state = self._state(
            update,
            state.book_id,
            state.strikes_thousandths,
            state.strikes,
            tuple(call),
            tuple(put),
            tuple(gex),
            index,
            None,
        )
        return self.state, False

    def _desync(self) -> tuple[None, bool]:
        self.awaiting_keyframe = True
        return None, True

    def _state(
        self,
        header: GexStrikeBook | GexStrikeBookUpdate,
        book_id: int,
        thousandths: tuple[int, ...],
        strikes: tuple[float, ...],
        call: tuple[float, ...],
        put: tuple[float, ...],
        gex: tuple[float, ...],
        changed: tuple[int, ...],
        reason: str | None,
    ) -> GexBookState:
        return GexBookState(
            symbol=self.symbol,
            book_id=book_id,
            version=header.version,
            captured_at=_utc(header.captured_at_ms),
            spot=header.spot,
            net_gex=header.net_gex,
            flip_point=header.flip_point if header.HasField("flip_point") else None,
            strikes=strikes,
            strikes_thousandths=thousandths,
            call_gex=call,
            put_gex=put,
            gex=gex,
            changed=changed,
            reason=reason,
        )


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _utc(milliseconds: int) -> datetime:
    return _EPOCH + timedelta(milliseconds=milliseconds)
