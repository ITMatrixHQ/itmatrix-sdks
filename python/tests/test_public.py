import asyncio
import threading
import unittest
from datetime import date

import httpx

import itmatrix as itm
from itmatrix._core._wire.rest_pb2 import ChainResponse, GexGridResponse, ReplayResponse

GRID = {
    "spot": None,
    "net_gex": 12.0,
    "max_abs_gex": 12.0,
    "strikes": [{"strike": 600125, "gex": 12.0, "call_oi": 0, "expiry": "1970-01-01"}],
}


class PublicClientTests(unittest.TestCase):
    def test_sync_reuses_one_loop_and_pool_closes_once(self):
        loops = []
        threads = []

        def handler(request):
            loops.append(asyncio.get_running_loop())
            threads.append(threading.get_ident())
            self.assertEqual(request.headers["authorization"], "Bearer secret")
            self.assertEqual(request.url.path, "/v2/gex/SPY/grid")
            return httpx.Response(200, json={"data": GRID, "meta": {"origin": "adhoc"}})

        with itm.ITMClient(
            api_key="secret", http_transport=httpx.MockTransport(handler)
        ) as client:
            for _ in range(3):
                result = client.get_gex("spy", top=3)
                self.assertIsInstance(result.data, itm.GexGrid)
                self.assertEqual(result.data.strikes[0].strike, 600.125)
                self.assertTrue(result.data.adhoc)
        self.assertEqual(len(set(loops)), 1)
        self.assertEqual(len(set(threads)), 1)
        self.assertNotEqual(threads[0], threading.get_ident())
        self.assertTrue(loops[0].is_closed())
        client.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            client.get_gex("SPY")

    def test_invalid_constructor_cleans_up_portal(self):
        before = {t.ident for t in threading.enumerate()}
        with self.assertRaises(ValueError):
            itm.ITMClient(base_url="https://user:secret@example.com")
        self.assertEqual(before, {t.ident for t in threading.enumerate()})


class AsyncPublicTests(unittest.IsolatedAsyncioTestCase):
    async def test_json_and_protobuf_gex_have_identical_models(self):
        wire = GexGridResponse(net_gex=12, max_abs_gex=12)
        wire.rows.add(strike_thousandths=600125, gex=12, call_oi=0, expiry_epoch_day=0)
        async with itm.AsyncITMClient(
            http_transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"data": GRID})
            )
        ) as client:
            json_result = await client.get_gex("SPY")
        async with itm.AsyncITMClient(
            transport="protobuf",
            http_transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    content=wire.SerializeToString(),
                    headers={"content-type": "application/x-protobuf"},
                )
            ),
        ) as client:
            binary_result = await client.get_gex("SPY")
        self.assertEqual(json_result.data, binary_result.data)
        self.assertIsNone(binary_result.data.spot)
        self.assertEqual(binary_result.data.strikes[0].call_oi, 0)
        self.assertIsNone(binary_result.data.strikes[0].put_oi)

    async def test_chain_real_json_shape_and_scaled_protobuf(self):
        value = {
            "spot": 600,
            "captured_at": 1234567890123,
            "partial": True,
            "rows": [
                {
                    "contract": {
                        "underlying": "SPY",
                        "right": "put",
                        "expiry": "2026-09-18",
                        "strike": 600125,
                    },
                    "oi": 9007199254740993,
                    "bid": 1.25,
                    "shares_per_contract": 100,
                }
            ],
        }
        async with itm.AsyncITMClient(
            http_transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"data": value})
            )
        ) as client:
            result = await client.get_option_chain("SPY")
        wire = ChainResponse(spot=600, captured_at_ms=1234567890123, partial=True)
        wire.rows.add(
            expiry_epoch_day=(date(2026, 9, 18) - date(1970, 1, 1)).days,
            right=1,
            strike_thousandths=600125,
            oi=9007199254740993,
            bid=1.25,
            shares_per_contract=100,
        )
        async with itm.AsyncITMClient(
            transport="protobuf",
            http_transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    content=wire.SerializeToString(),
                    headers={"content-type": "application/x-protobuf"},
                )
            ),
        ) as client:
            binary = await client.get_option_chain("SPY")
        self.assertEqual(binary.data.rows[0].oi, result.data.rows[0].oi)
        self.assertEqual(binary.data.rows[0].contract.expiry, "2026-09-18")
        self.assertIsNone(binary.data.rows[0].contract.underlying)
        self.assertEqual(binary.data.captured_at_ms, result.data.captured_at_ms)
        self.assertTrue(binary.data.partial)

    async def test_no_credentials_sent_to_absolute_or_traversal_url(self):
        calls = []
        async with itm.AsyncITMClient(
            api_key="secret",
            http_transport=httpx.MockTransport(lambda r: calls.append(r)),
        ) as client:
            for path in (
                "https://evil.test/v2/gex/SPY/grid",
                "//evil.test/v2/",
                "/v2/../secret",
                "/v2/%2e%2e/secret",
                "/v2/x?redirect=evil",
                "/v2/x#fragment",
            ):
                with self.assertRaises(ValueError):
                    await client.request(path)
        self.assertEqual(calls, [])

    async def test_redirects_not_followed_even_with_injected_client(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(302, headers={"location": "https://evil.test/v2/"})

        async with httpx.AsyncClient(
            follow_redirects=True, transport=httpx.MockTransport(handler)
        ) as http:
            async with itm.AsyncITMClient(api_key="secret", http_client=http) as client:
                with self.assertRaises(itm.ITMError):
                    await client.request("/v2/symbols")
            self.assertFalse(http.is_closed)
        self.assertEqual(len(calls), 1)

    async def test_sync_client_can_be_used_inside_running_loop(self):
        with itm.ITMClient(
            http_transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"data": GRID})
            )
        ) as client:
            self.assertEqual(client.get_gex("SPY").data.net_gex, 12)

    async def test_replay_normalizes_prices_without_truncating_sequences(self):
        wire = ReplayResponse(
            price_scale=10000, fidelity=0, complete=True, sources=["trade"]
        )
        wire.ticks.add(
            ts_ms=1234567890123, price_scaled=12500, source_id=0, seq=9007199254740993
        )
        async with itm.AsyncITMClient(
            transport="protobuf",
            http_transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    content=wire.SerializeToString(),
                    headers={"content-type": "application/x-protobuf"},
                )
            ),
        ) as client:
            result = await client.get_replay("SPY", date="2026-09-04")
        self.assertEqual(result.data.events[0].price, 1.25)
        self.assertEqual(result.data.events[0].seq, 9007199254740993)
        self.assertEqual(result.data.session, "2026-09-04")
