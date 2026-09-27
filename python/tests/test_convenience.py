import unittest
from datetime import datetime, timezone

import httpx

import itmatrix as itm
from itmatrix.models import GexGrid, GexStrike


class LocalAnalyticsTests(unittest.TestCase):
    def test_gex_analysis_keeps_server_scalars_and_groups_visible_strikes(self) -> None:
        grid = GexGrid(
            strikes=(
                GexStrike(600_000, 7.0, expiry="2026-09-27"),
                GexStrike(600_000, -2.0, expiry="2026-10-02"),
                GexStrike(610_000, -9.0),
                GexStrike(590_000, 5.0),
            ),
            net_gex=123.0, max_abs_gex=50.0, spot=602.0,
            flip_point=None,
        )
        result = itm.analyze_gex(grid, levels=2)
        self.assertEqual(result.net_gex, 123.0)  # never sum a trimmed list
        self.assertEqual(result.regime, "positive")
        self.assertIsNone(result.zero_gamma)  # no invented zero-gamma level
        self.assertEqual([row.strike for row in result.positive_levels], [590.0, 600.0])
        self.assertEqual(result.positive_levels[1].gex, 5.0)
        self.assertEqual(result.negative_levels[0].gex, -9.0)
        self.assertEqual(result.positive_levels[0].distance_from_spot, -12.0)
        self.assertEqual(result.levels_scope, "returned_rows")

    def test_calendar_windows_are_new_york_dates(self) -> None:
        now = datetime(2026, 9, 27, 1, 0, tzinfo=timezone.utc)
        self.assertEqual(
            (w := itm.resolve_bar_window("today", now=now)).start.isoformat(),
            "2026-09-26",
        )
        self.assertEqual(w.end, w.start)
        self.assertEqual(
            itm.resolve_bar_window("calendar_month", month="2024-02", now=now).end.isoformat(),
            "2024-02-29",
        )
        with self.assertRaisesRegex(ValueError, "cannot cover"):
            from itmatrix.periods import validate_bar_window

            validate_bar_window(
                itm.resolve_bar_window("calendar_month", month="2026-09", now=now),
                "1m",
            )


class ConvenienceClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_gex_analysis_preserves_response_metadata(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v2/gex/SPY/grid")
            return httpx.Response(
                200,
                json={"data": {"strikes": [{"strike": 600_000, "gex": 5.0}],
                               "net_gex": -10.0, "max_abs_gex": 5.0,
                               "spot": 601.0, "flip_point": 600.5},
                      "meta": {"date": "2026-09-26"}},
                headers={"x-request-id": "convenience-1"},
            )

        client = itm.AsyncITMClient(http_transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.aclose)
        result = await client.get_gex_analysis("spy")
        self.assertEqual(result.data.net_gex, -10.0)
        self.assertEqual(result.data.regime, "negative")
        self.assertEqual(result.data.zero_gamma, 600.5)
        self.assertEqual(result.request_id, "convenience-1")
        self.assertEqual(result.meta["date"], "2026-09-26")

    async def test_gex_shares_match_over_json_and_protobuf(self) -> None:
        from itmatrix._core._wire.rest_pb2 import GexGridResponse

        wire = GexGridResponse(spot=600.0, net_gex=6.0e8, max_abs_gex=6.0e8,
                               net_gex_shares=1.1e6, max_abs_gex_shares=1.1e6)
        wire.rows.add(strike_thousandths=600_000, gex=6.0e8, gex_shares=1.1e6,
                      delta_adj=1.5e8, delta_adj_shares=2.5e5)
        body = {"spot": 600.0, "prior_close_spot": None, "captured_at": None,
                "net_gex": 6.0e8, "net_gex_shares": 1.1e6, "flip_point": None,
                "max_abs_gex": 6.0e8, "max_abs_gex_shares": 1.1e6,
                "strikes": [{"strike": 600_000, "gex": 6.0e8, "gex_shares": 1.1e6,
                             "delta_adj": 1.5e8, "delta_adj_shares": 2.5e5}]}

        def handler(request: httpx.Request) -> httpx.Response:
            if "protobuf" in request.headers.get("accept", ""):
                return httpx.Response(
                    200, content=wire.SerializeToString(),
                    headers={"content-type": "application/x-protobuf"})
            return httpx.Response(200, json={"data": body, "meta": {}})

        grids = []
        for transport in ("json", "protobuf"):
            client = itm.AsyncITMClient(transport=transport,
                                        http_transport=httpx.MockTransport(handler))
            self.addAsyncCleanup(client.aclose)
            grids.append((await client.get_gex("SPY")).data)
        self.assertEqual(grids[0], grids[1])
        self.assertEqual(grids[1].net_gex_shares, 1.1e6)
        self.assertEqual(grids[1].strikes[0].gex_shares, 1.1e6)
        self.assertEqual(grids[1].strikes[0].delta_adj_shares, 2.5e5)

    async def test_gex_history_carries_share_fields(self) -> None:
        capture = {"captured_at": 1, "spot": 500.0, "prior_close_spot": None,
                   "net_gex": 1.0e9, "net_gex_shares": 2.0e6, "flip_point": None,
                   "strikes": [{"strike": 500_000, "gex": 1.0e9, "gex_shares": 2.0e6,
                                "delta_adj": None, "delta_adj_shares": None}]}

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v2/gex/SPY/history")
            return httpx.Response(200, json={"data": {
                "symbol": "SPY", "session_date": "2026-09-24", "captures": [capture]},
                "meta": {}})

        client = itm.AsyncITMClient(http_transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.aclose)
        result = await client.get_gex_history("SPY", date="2026-09-24")
        first = result.data["captures"][0]
        self.assertEqual(first["net_gex_shares"], 2.0e6)
        self.assertEqual(first["strikes"][0]["gex_shares"], 2.0e6)
        self.assertIsNone(first["strikes"][0]["delta_adj_shares"])

    async def test_period_helper_sends_inclusive_ny_dates(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v2/stocks/SPY/bars")
            self.assertEqual(request.url.params["from"], "2024-02-01")
            self.assertEqual(request.url.params["to"], "2024-02-29")
            self.assertEqual(request.url.params["timeframe"], "1d")
            self.assertEqual(request.url.params["tz"], "America/New_York")
            return httpx.Response(200, json={"data": [], "meta": {}})

        client = itm.AsyncITMClient(http_transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.aclose)
        result = await client.get_bars_for_period(
            "SPY", "calendar_month", month="2024-02"
        )
        self.assertEqual(result.data.bars, ())
