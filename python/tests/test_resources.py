import json
import unittest
from pathlib import Path

import httpx

import itmatrix as itm
from itmatrix._core import ITMClient
from itmatrix._core._wire.rest_pb2 import (
    OffExchangeActivityResponse,
    OffExchangeActivityRow,
)
from itmatrix._core.resources import (
    INTENTIONALLY_UNSUPPORTED_OPERATIONS,
    SUPPORTED_OPERATIONS,
)


class ContractCoverageTests(unittest.TestCase):
    def test_every_non_internal_v2_operation_is_covered(self) -> None:
        path = Path(__file__).resolve().parents[2] / "spec" / "openapi.json"
        spec = json.loads(path.read_text())
        expected = {
            operation["operationId"]
            for route, methods in spec["paths"].items()
            if route.startswith("/v2/")
            for operation in methods.values()
            if operation.get("operationId")
            and operation.get("x-exposure") != "internal"
        }
        self.assertEqual(
            SUPPORTED_OPERATIONS | INTENTIONALLY_UNSUPPORTED_OPERATIONS, expected
        )


class ResourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_offexchange_protobuf_is_negotiated_and_decoded(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["accept"], "application/x-protobuf")
            body = OffExchangeActivityResponse(
                rows=[OffExchangeActivityRow(date="2026-09-25", trade_count=3)]
            ).SerializeToString()
            return httpx.Response(
                200,
                content=body,
                headers={"content-type": "application/x-protobuf"},
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(http.aclose)
        client = ITMClient(http_client=http, transport="protobuf")
        result = await client.offexchange.activity("spy")
        self.assertIsInstance(result.data, OffExchangeActivityResponse)
        self.assertEqual(result.data.rows[0].trade_count, 3)

    async def test_offexchange_resource_only_calls_explicit_synthetic_endpoints(
        self,
    ) -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(200, json={"data": [], "meta": {}})

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(http.aclose)
        client = ITMClient(http_client=http)

        await client.offexchange.concentration("spy", limit=20)
        self.assertEqual(calls[0].url.path, "/v2/offexchange/SPY/concentration")
        self.assertEqual(calls[0].url.params["limit"], "20")
        self.assertIs(client.darkpool, client.offexchange)
        self.assertFalse(hasattr(client.offexchange, "dataset"))
        self.assertFalse(hasattr(client.offexchange, "prints"))

    async def test_flow_queries_are_constrained_and_use_wire_names(self) -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(200, json={"data": {}, "meta": {}})

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(http.aclose)
        client = ITMClient(http_client=http)

        with self.assertRaisesRegex(ValueError, "symbol or min_premium_usd"):
            await client.flow.large_trades()
        with self.assertRaisesRegex(ValueError, "symbol or min_premium_usd"):
            await client.flow.eod(session="2026-09-25")

        await client.flow.large_trades(
            min_premium_usd=250_000,
            session="2026-09-26",
            limit=25,
        )
        await client.flow.eod(
            session="2026-09-25",
            symbol="spy",
            right="call",
        )
        premium = await client.flow.premium(
            "spy",
            sessions=2,
            bucket="5m",
            min_premium_usd=100_000,
            dte="zero",
            to_ms=1_790_000_000_000,
        )

        self.assertEqual(calls[0].url.path, "/v2/flow/large-trades")
        self.assertEqual(calls[0].url.params["min_premium_usd"], "250000")
        self.assertEqual(calls[0].url.params["limit"], "25")
        self.assertEqual(calls[1].url.path, "/v2/flow/eod")
        self.assertEqual(calls[1].url.params["session"], "2026-09-25")
        self.assertEqual(calls[1].url.params["symbol"], "SPY")
        self.assertEqual(calls[1].url.params["right"], "call")
        self.assertEqual(calls[2].url.path, "/v2/flow/premium")
        self.assertEqual(calls[2].url.params["symbol"], "SPY")
        self.assertEqual(calls[2].url.params["sessions"], "2")
        self.assertEqual(calls[2].url.params["bucket"], "5m")
        self.assertEqual(calls[2].url.params["min_premium_usd"], "100000")
        self.assertEqual(calls[2].url.params["dte"], "zero")
        self.assertEqual(calls[2].url.params["to_ms"], "1790000000000")
        self.assertEqual(premium.data, {})

    async def test_gex_reference_uses_exact_session_and_basis(self) -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "symbol": "SPY",
                        "basis": "prev_close",
                        "session_date": "2026-09-25",
                        "reference_session_date": "2026-09-24",
                        "captured_at": None,
                        "spot": None,
                        "net_gex": None,
                        "complete": None,
                        "contract_count": None,
                        "gamma_absent": None,
                        "strikes": [],
                    },
                    "meta": {"available": False},
                },
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(http.aclose)
        client = ITMClient(http_client=http)
        result = await client.gex_reference(
            "spy", date="2026-09-25", basis="prev_close"
        )

        self.assertEqual(calls[0].url.path, "/v2/gex/SPY/reference")
        self.assertEqual(calls[0].url.params["date"], "2026-09-25")
        self.assertEqual(calls[0].url.params["basis"], "prev_close")
        self.assertIsNone(result.data["captured_at"])
        self.assertEqual(result.data["strikes"], [])

    async def test_grouped_resources_text_body_and_expirations(self) -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if request.url.path.endswith(".proto"):
                return httpx.Response(
                    200,
                    text='syntax = "proto3";',
                    headers={"content-type": "text/plain"},
                )
            return httpx.Response(200, json={"data": {"ok": True}, "meta": {}})

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(http.aclose)
        client = ITMClient(http_client=http)

        await client.account.submit_classification("non_professional")
        await client.journal.import_trades(
            '{"symbol":"SPY"}\n',
            import_id="import-1",
        )
        await client.reference.get("spy")
        await client.expirations(
            "spy",
            at="2026-08-29",
            expiry="2026-09-18",
            strike_gte=500_000,
        )
        schema = await client.protocols.websocket_schema()

        self.assertEqual(calls[0].url.path, "/v2/account/classification")
        self.assertEqual(calls[0].method, "POST")
        self.assertEqual(
            json.loads(calls[0].content),
            {"classification": "non_professional"},
        )

        self.assertEqual(calls[1].url.params["import_id"], "import-1")
        self.assertEqual(calls[1].headers["content-type"], "text/plain")
        self.assertEqual(calls[1].content, b'{"symbol":"SPY"}\n')

        self.assertEqual(calls[2].url.path, "/v2/symbols/SPY")
        self.assertEqual(calls[3].url.path, "/v2/chain/SPY/expirations")
        self.assertEqual(calls[3].url.params["at"], "2026-08-29")
        self.assertEqual(calls[3].url.params["expiry"], "2026-09-18")
        self.assertEqual(calls[3].url.params["strike_gte"], "500000")
        self.assertNotIn("date", calls[3].url.params)
        self.assertEqual(calls[4].headers["accept"], "text/plain")
        self.assertIn("proto3", schema.data)

    async def test_etag_and_not_modified(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(
                    200,
                    json={"data": {"symbol": "SPY"}, "meta": {}},
                    headers={"etag": '"symbol-v1"'},
                )
            self.assertEqual(request.headers["if-none-match"], '"symbol-v1"')
            return httpx.Response(
                304,
                headers={"etag": '"symbol-v1"'},
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(http.aclose)
        client = ITMClient(http_client=http)

        fresh = await client.request("/v2/symbols/SPY")
        unchanged = await client.request(
            "/v2/symbols/SPY",
            if_none_match=fresh.etag,
        )

        self.assertEqual(fresh.etag, '"symbol-v1"')
        self.assertEqual(unchanged.status, 304)
        self.assertIsNone(unchanged.data)
        self.assertTrue(unchanged.not_modified)
        self.assertEqual(unchanged.etag, '"symbol-v1"')


class SyncResourceTests(unittest.TestCase):
    def test_sync_facade_exposes_only_explicit_offexchange_methods(self) -> None:
        with itm.ITMClient(api_key="offline") as client:
            self.assertTrue(hasattr(client, "offexchange"))
            self.assertFalse(hasattr(client.offexchange, "dataset"))


if __name__ == "__main__":
    unittest.main()
