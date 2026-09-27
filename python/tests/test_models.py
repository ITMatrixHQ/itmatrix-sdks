import math
import pickle
import unittest
from dataclasses import FrozenInstanceError, asdict, replace

from itmatrix import GexGrid, GexStrike
from itmatrix._core._wire.rest_pb2 import GexGridResponse


class GexModelTests(unittest.TestCase):
    def test_every_optional_presence_combination_remains_a_frozen_dataclass(self):
        wire = GexGridResponse(net_gex=-0.0, max_abs_gex=1)
        fields = {"gex_0dte": -0.0, "call_oi": 0, "put_oi": -2147483648,
                  "delta_adj": -0.0, "expiry_epoch_day": 0}
        expected = []
        for mask in range(32):
            values = {name: value for bit, (name, value) in enumerate(fields.items())
                      if mask & (1 << bit)}
            wire.rows.add(strike_thousandths=9223372036854775807, gex=-0.0, **values)
            expected.append(GexStrike(
                9223372036854775807, -0.0, values.get("gex_0dte"),
                values.get("call_oi"), values.get("put_oi"), values.get("delta_adj"),
                "1970-01-01" if "expiry_epoch_day" in values else None))
        actual = GexGrid.decode(wire)
        self.assertEqual(actual.strikes, tuple(expected))
        for row, reference in zip(actual.strikes, expected):
            self.assertIs(type(row), GexStrike)
            self.assertEqual(asdict(row), asdict(reference))
            self.assertEqual(hash(row), hash(reference))
            self.assertEqual(pickle.loads(pickle.dumps(row)), reference)
            self.assertEqual(replace(row, gex=12).gex, 12)
            self.assertEqual(math.copysign(1, row.gex), -1)
            with self.assertRaises(FrozenInstanceError):
                row.gex = 3
        wire.rows[0].gex = 999
        self.assertEqual(actual.strikes[0].gex, -0.0)

    def test_json_and_wire_eager_rows_remain_identical(self):
        wire = GexGridResponse(net_gex=3, max_abs_gex=3)
        wire.rows.add(strike_thousandths=125000, gex=3, call_oi=0)
        data = {"net_gex": 3, "max_abs_gex": 3, "strikes": [
            {"strike": 125000, "gex": 3, "call_oi": 0}]}
        actual = GexGrid.decode(data)
        self.assertEqual(actual, GexGrid.decode(wire))
        self.assertEqual(actual.strikes[0].strike, 125)
        data["strikes"][0]["gex"] = 999
        self.assertEqual(actual.strikes[0].gex, 3)

    def test_share_siblings_decode_identically_from_json_and_protobuf(self):
        wire = GexGridResponse(net_gex=3.0e9, max_abs_gex=4.0e9,
                               net_gex_shares=5.0e6, max_abs_gex_shares=6.5e6)
        wire.rows.add(strike_thousandths=600000, gex=4.0e9, gex_shares=6.5e6,
                      gex_0dte=1.0e9, gex_0dte_shares=1.6e6,
                      delta_adj=2.0e9, delta_adj_shares=3.3e6)
        wire.rows.add(strike_thousandths=590000, gex=-1.0e9, gex_shares=-1.5e6)
        data = {"net_gex": 3.0e9, "max_abs_gex": 4.0e9,
                "net_gex_shares": 5.0e6, "max_abs_gex_shares": 6.5e6,
                "strikes": [
                    {"strike": 600000, "gex": 4.0e9, "gex_shares": 6.5e6,
                     "gex_0dte": 1.0e9, "gex_0dte_shares": 1.6e6,
                     "delta_adj": 2.0e9, "delta_adj_shares": 3.3e6},
                    {"strike": 590000, "gex": -1.0e9, "gex_shares": -1.5e6,
                     "delta_adj": None, "delta_adj_shares": None},
                ]}
        from_json, from_wire = GexGrid.decode(data), GexGrid.decode(wire)
        self.assertEqual(from_json, from_wire)
        self.assertEqual(from_json.net_gex_shares, 5.0e6)
        self.assertEqual(from_json.max_abs_gex_shares, 6.5e6)
        row = from_wire.strikes[0]
        self.assertEqual((row.gex_shares, row.gex_0dte_shares, row.delta_adj_shares),
                         (6.5e6, 1.6e6, 3.3e6))
        self.assertIsNone(from_wire.strikes[1].delta_adj_shares)
        self.assertIsNone(from_wire.strikes[1].gex_0dte_shares)

    def test_unknown_shares_stay_none_on_both_transports(self):
        wire = GexGridResponse(net_gex=1, max_abs_gex=1)
        wire.rows.add(strike_thousandths=600000, gex=1, delta_adj=0.5)
        data = {"net_gex": 1, "max_abs_gex": 1, "net_gex_shares": None,
                "max_abs_gex_shares": None,
                "strikes": [{"strike": 600000, "gex": 1, "gex_shares": None,
                             "delta_adj": 0.5, "delta_adj_shares": None}]}
        for grid in (GexGrid.decode(wire), GexGrid.decode(data)):
            self.assertIsNone(grid.net_gex_shares)
            self.assertIsNone(grid.max_abs_gex_shares)
            self.assertIsNone(grid.strikes[0].gex_shares)
            self.assertIsNone(grid.strikes[0].delta_adj_shares)
        self.assertEqual(GexGrid.decode(wire), GexGrid.decode(data))

    def test_explicit_zero_shares_are_not_unknown(self):
        wire = GexGridResponse(net_gex=0, max_abs_gex=0, net_gex_shares=0.0)
        wire.rows.add(strike_thousandths=600000, gex=0, gex_shares=-0.0)
        grid = GexGrid.decode(wire)
        self.assertEqual(grid.net_gex_shares, 0.0)
        self.assertEqual(grid.strikes[0].gex_shares, 0.0)
        self.assertEqual(math.copysign(1, grid.strikes[0].gex_shares), -1)

    def test_empty_and_repeated_expiry_grids(self):
        wire = GexGridResponse()
        self.assertEqual(GexGrid.decode(wire).strikes, ())
        for _ in range(3):
            wire.rows.add(expiry_epoch_day=0)
        self.assertEqual([r.expiry for r in GexGrid.decode(wire).strikes],
                         ["1970-01-01"] * 3)
        wire.rows[0].expiry_epoch_day = -1
        self.assertEqual(GexGrid.decode(wire).strikes[0].expiry, "1969-12-31")
