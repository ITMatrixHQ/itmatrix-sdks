"""GEX book v2 reconstruction against the golden wire vectors.

``fixtures/gex_strike_book_vectors.json`` is the backend's fixture, copied
verbatim. The numbers are synthetic. Every other book test builds on these bytes.
"""

import json
import struct
import unittest
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from itmatrix._core._wire.ws_pb2 import Frame, GexBookReason
from itmatrix._core.gex_book import GexBookState, GexBookTracker, book_crc

VECTORS = json.loads(
    (Path(__file__).parent / "fixtures" / "gex_strike_book_vectors.json").read_text()
)
STEPS = {step["name"]: step for step in VECTORS["steps"]}
KEYFRAME = STEPS["keyframe"]
UPDATE_1 = STEPS["update_1"]
UPDATE_2 = STEPS["update_2"]
MISMATCH = STEPS["update_crc_mismatch"]
BOOK_ID = int(KEYFRAME["state"]["book_id"])


def frame(step: dict[str, Any]) -> Frame:
    return Frame.FromString(bytes.fromhex(step["frame_hex"]))


def keyframe_at(version: int, book_id: int = BOOK_ID) -> Frame:
    value = frame(KEYFRAME)
    value.gex_strike_book.version = version
    value.gex_strike_book.book_id = book_id
    return value


def assert_state(test: unittest.TestCase, state: GexBookState, expected: dict) -> None:
    test.assertEqual(state.symbol, VECTORS["symbol"])
    test.assertEqual(state.book_id, int(expected["book_id"]))
    test.assertEqual(state.version, expected["version"])
    test.assertEqual(
        state.captured_at,
        datetime.fromtimestamp(expected["captured_at_ms"] / 1000, tz=timezone.utc),
    )
    test.assertEqual(state.spot, expected["spot"])
    test.assertEqual(state.net_gex, expected["net_gex"])
    test.assertEqual(state.flip_point, expected["flip_point"])
    test.assertEqual(list(state.strikes_thousandths), expected["strikes"])
    test.assertEqual(list(state.strikes), [s / 1000 for s in expected["strikes"]])
    test.assertEqual(list(state.call_gex), expected["call_gex"])
    test.assertEqual(list(state.put_gex), expected["put_gex"])
    test.assertEqual(list(state.gex), expected["gex"])
    test.assertEqual(
        book_crc(state.strikes_thousandths, state.call_gex, state.put_gex),
        expected["crc"],
    )


class VectorTests(unittest.TestCase):
    def test_every_frame_decodes_and_reencodes_byte_exact(self) -> None:
        for step in VECTORS["steps"]:
            with self.subTest(step["name"]):
                value = frame(step)
                self.assertEqual(value.SerializeToString().hex(), step["frame_hex"])
                self.assertEqual(value.symbol, step["frame"]["symbol"])
                kind = value.WhichOneof("payload")
                self.assertIn(kind, step["frame"])
                expected = step["frame"][kind]
                message = getattr(value, kind)
                self.assertEqual(message.captured_at_ms, expected["captured_at_ms"])
                self.assertEqual(message.book_id, int(expected["book_id"]))
                self.assertEqual(message.version, expected["version"])
                self.assertEqual(message.spot, expected["spot"])
                self.assertEqual(message.net_gex, expected["net_gex"])
                if expected["flip_point"] is None:
                    self.assertFalse(message.HasField("flip_point"))
                else:
                    self.assertEqual(message.flip_point, expected["flip_point"])
                self.assertEqual(list(message.call_gex), expected["call_gex"])
                self.assertEqual(list(message.put_gex), expected["put_gex"])
                self.assertEqual(message.crc, expected["crc"])
                if kind == "gex_strike_book":
                    self.assertEqual(list(message.strikes), expected["strikes_delta"])
                    self.assertEqual(message.reason, expected["reason"])
                else:
                    self.assertEqual(list(message.index), expected["index"])

    def test_keyframe_is_frame_field_23_and_update_field_24(self) -> None:
        # Field 23, wire type 2: tag varint 0xba 0x01. Field 24: 0xc2 0x01.
        self.assertTrue(KEYFRAME["frame_hex"].startswith("0a03535059ba01"))
        self.assertTrue(UPDATE_1["frame_hex"].startswith("0a03535059c201"))
        self.assertEqual(frame(KEYFRAME).WhichOneof("payload"), "gex_strike_book")
        self.assertEqual(
            frame(KEYFRAME).gex_strike_book.reason,
            GexBookReason.GEX_BOOK_REASON_SUBSCRIBE,
        )

    def test_crc_is_zlib_crc32_over_strike_call_put(self) -> None:
        state = KEYFRAME["state"]
        raw = b"".join(
            struct.pack("<qff", s, c, p)
            for s, c, p in zip(state["strikes"], state["call_gex"], state["put_gex"])
        )
        self.assertEqual(zlib.crc32(raw), state["crc"])
        self.assertEqual(
            book_crc(state["strikes"], state["call_gex"], state["put_gex"]),
            state["crc"],
        )
        self.assertEqual(book_crc([], [], []), 0)

    def test_crc_after_applying_the_mismatch_frame(self) -> None:
        before = MISMATCH["state"]
        update = MISMATCH["frame"]["gex_strike_book_update"]
        call, put = list(before["call_gex"]), list(before["put_gex"])
        for i, c, p in zip(update["index"], update["call_gex"], update["put_gex"]):
            call[i], put[i] = c, p
        self.assertEqual(
            book_crc(before["strikes"], call, put), MISMATCH["crc_after_applying"]
        )
        self.assertNotEqual(MISMATCH["crc_after_applying"], update["crc"])


class TrackerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.book = GexBookTracker("SPY")

    def keyframe(self) -> GexBookState:
        state, resync = self.book.apply(frame(KEYFRAME))
        assert state is not None and not resync
        return state

    def test_steps_in_order_reach_every_expected_state(self) -> None:
        for step in VECTORS["steps"]:
            with self.subTest(step["name"]):
                before = self.book.state
                state, resync = self.book.apply(frame(step))
                if step["expect"] == "apply":
                    self.assertFalse(resync)
                    assert state is not None
                    self.assertIs(self.book.state, state)
                else:
                    self.assertEqual(step["expect"], "resync")
                    self.assertEqual((state, resync), (None, True))
                    self.assertIs(self.book.state, before)
                    self.assertTrue(self.book.awaiting_keyframe)
                assert self.book.state is not None
                assert_state(self, self.book.state, step["state"])

    def test_keyframe_state(self) -> None:
        state = self.keyframe()
        self.assertEqual(state.strikes, (595.0, 600.0, 602.5, 605.0, 610.0))
        self.assertEqual(state.changed, (0, 1, 2, 3, 4))
        self.assertEqual(state.reason, "subscribe")
        # No open interest anywhere in the state.
        self.assertFalse(hasattr(state, "call_oi"))
        self.assertFalse(hasattr(state, "put_oi"))

    def test_update_marks_changed_positions_and_carries_strikes(self) -> None:
        first = self.keyframe()
        state, _ = self.book.apply(frame(UPDATE_1))
        assert state is not None
        self.assertEqual(state.changed, (1, 4))
        self.assertIsNone(state.reason)
        self.assertIs(state.strikes_thousandths, first.strikes_thousandths)
        state, _ = self.book.apply(frame(UPDATE_2))
        assert state is not None
        self.assertEqual(state.changed, (0, 2))
        self.assertIsNone(state.flip_point)

    def test_gex_is_float64_sum_of_the_float32_sides(self) -> None:
        self.keyframe()
        self.book.apply(frame(UPDATE_1))
        state, _ = self.book.apply(frame(UPDATE_2))
        assert state is not None
        for c, p, total in zip(state.call_gex, state.put_gex, state.gex):
            self.assertEqual(total, float(c) + float(p))
        # 12_600_000 - 140_000_000: exact in float64.
        self.assertEqual(state.gex[0], -127_400_000.0)

    def test_crc_mismatch_leaves_the_book_untouched_and_resyncs(self) -> None:
        self.keyframe()
        self.book.apply(frame(UPDATE_1))
        self.book.apply(frame(UPDATE_2))
        before = self.book.state
        self.assertEqual(self.book.apply(frame(MISMATCH)), (None, True))
        self.assertIs(self.book.state, before)
        # The same update with the right CRC is now dropped: waiting for a keyframe.
        fixed = frame(MISMATCH)
        fixed.gex_strike_book_update.crc = MISMATCH["crc_after_applying"]
        self.assertEqual(self.book.apply(fixed), (None, True))
        state, resync = self.book.apply(keyframe_at(9))
        self.assertFalse(resync)
        assert state is not None
        self.assertFalse(self.book.awaiting_keyframe)

    def test_the_corrected_mismatch_frame_applies(self) -> None:
        self.keyframe()
        self.book.apply(frame(UPDATE_1))
        self.book.apply(frame(UPDATE_2))
        fixed = frame(MISMATCH)
        fixed.gex_strike_book_update.crc = MISMATCH["crc_after_applying"]
        state, resync = self.book.apply(fixed)
        self.assertFalse(resync)
        assert state is not None
        self.assertEqual((state.version, state.changed), (10, (3,)))
        self.assertEqual(state.call_gex[3], 64_000_000.0)
        self.assertEqual(state.put_gex[3], -15_500_000.0)

    def test_duplicate_is_ignored_without_a_resync(self) -> None:
        self.keyframe()
        self.book.apply(frame(UPDATE_1))
        self.assertEqual(self.book.apply(frame(UPDATE_1)), (None, False))
        older = frame(UPDATE_1)
        older.gex_strike_book_update.version = 5
        self.assertEqual(self.book.apply(older), (None, False))
        self.assertFalse(self.book.awaiting_keyframe)

    def test_gap_resyncs_and_drops_updates_until_a_keyframe(self) -> None:
        self.keyframe()
        # Version 9 arrives while the book is at 7: version 8 was lost.
        self.assertEqual(self.book.apply(frame(UPDATE_2)), (None, True))
        self.assertTrue(self.book.awaiting_keyframe)
        # Version 8 is now in order, but updates are dropped until a keyframe.
        self.assertEqual(self.book.apply(frame(UPDATE_1)), (None, True))
        state, resync = self.book.apply(keyframe_at(9))
        self.assertFalse(resync)
        assert state is not None
        self.assertEqual(state.version, 9)
        self.assertFalse(self.book.awaiting_keyframe)

    def test_other_book_id_resyncs(self) -> None:
        self.keyframe()
        other = frame(UPDATE_1)
        other.gex_strike_book_update.book_id = BOOK_ID + 1
        self.assertEqual(self.book.apply(other), (None, True))

    def test_new_book_id_in_a_keyframe_replaces_everything(self) -> None:
        self.keyframe()
        self.book.apply(frame(UPDATE_1))
        state, resync = self.book.apply(keyframe_at(1, book_id=BOOK_ID + 7))
        self.assertFalse(resync)
        assert state is not None
        self.assertEqual((state.book_id, state.version), (BOOK_ID + 7, 1))
        self.assertEqual(list(state.call_gex), KEYFRAME["state"]["call_gex"])

    def test_update_before_any_keyframe_is_dropped(self) -> None:
        self.assertEqual(self.book.apply(frame(UPDATE_1)), (None, True))

    def test_reset_forgets_the_book(self) -> None:
        self.keyframe()
        self.book.reset()
        self.assertIsNone(self.book.state)
        self.assertEqual(self.book.apply(frame(UPDATE_1)), (None, True))

    def test_malformed_updates_resync(self) -> None:
        self.keyframe()
        out_of_range = frame(UPDATE_1)
        out_of_range.gex_strike_book_update.index[1] = 9
        self.assertEqual(self.book.apply(out_of_range), (None, True))
        self.book.apply(keyframe_at(7))
        short_put = frame(UPDATE_1)
        del short_put.gex_strike_book_update.put_gex[1]
        self.assertEqual(self.book.apply(short_put), (None, True))
        self.book.apply(keyframe_at(7))
        short_call = frame(UPDATE_1)
        del short_call.gex_strike_book_update.call_gex[0]
        self.assertEqual(self.book.apply(short_call), (None, True))
        self.book.apply(keyframe_at(7))
        unordered = frame(UPDATE_1)
        unordered.gex_strike_book_update.index[:] = [4, 1]
        self.assertEqual(self.book.apply(unordered), (None, True))

    def test_corrupted_keyframe_is_not_adopted_and_resyncs(self) -> None:
        bad = frame(KEYFRAME)
        bad.gex_strike_book.crc ^= 1
        self.assertEqual(self.book.apply(bad), (None, True))
        self.assertIsNone(self.book.state)
        self.assertEqual(self.book.apply(frame(UPDATE_1)), (None, True))
        # A corrupted put value fails the same check, with the CRC left as sent.
        flipped = frame(KEYFRAME)
        flipped.gex_strike_book.put_gex[2] = -46_875_004.0
        self.assertEqual(self.book.apply(flipped), (None, True))
        state = self.keyframe()
        self.assertEqual(state.version, 7)
        self.assertFalse(self.book.awaiting_keyframe)

    def test_corrupted_keyframe_keeps_the_previous_book(self) -> None:
        before = self.keyframe()
        bad = keyframe_at(12)
        bad.gex_strike_book.crc ^= 1
        self.assertEqual(self.book.apply(bad), (None, True))
        self.assertIs(self.book.state, before)
        self.assertEqual(self.book.apply(frame(UPDATE_1)), (None, True))

    def test_malformed_keyframes_are_not_adopted(self) -> None:
        short = frame(KEYFRAME)
        del short.gex_strike_book.put_gex[0]
        self.assertEqual(self.book.apply(short), (None, True))
        descending = frame(KEYFRAME)
        descending.gex_strike_book.strikes[2] = -2_500
        self.assertEqual(self.book.apply(descending), (None, True))
        self.assertIsNone(self.book.state)
        self.keyframe()
        self.assertFalse(self.book.awaiting_keyframe)

    def test_a_frame_without_a_book_is_ignored(self) -> None:
        self.keyframe()
        self.assertEqual(self.book.apply(Frame(symbol="SPY")), (None, False))
        self.assertEqual(self.book.state.version, 7)  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
