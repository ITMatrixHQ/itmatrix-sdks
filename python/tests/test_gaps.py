"""The 0.4.0 gap list, mirrored from the TypeScript surface."""

import unittest

import httpx

import itmatrix as itm
from itmatrix._core import ITMClient


def _recorder(
    calls: list[httpx.Request], response: httpx.Response | None = None
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if response is not None:
            return response
        return httpx.Response(200, json={"data": {"ok": True}, "meta": {}})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class GexDteTests(unittest.IsolatedAsyncioTestCase):
    async def test_dte_is_sent_and_echoed(self) -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "spot": 600,
                        "net_gex": 1,
                        "max_abs_gex": 1,
                        "strikes": [],
                    },
                    "meta": {"dte": 0},
                },
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(http.aclose)
        client = ITMClient(http_client=http)

        result = await client.gex("SPY", dte=0, top=3)
        self.assertEqual(calls[0].url.params["dte"], "0")
        self.assertEqual(result.meta["dte"], 0)

    async def test_no_filter_sends_no_param(self) -> None:
        calls: list[httpx.Request] = []
        http = _recorder(calls)
        self.addAsyncCleanup(http.aclose)
        client = ITMClient(http_client=http)

        await client.gex("SPY")
        self.assertNotIn("dte", calls[0].url.params)


class FundamentalsTests(unittest.IsolatedAsyncioTestCase):
    async def test_statement_query_is_sent(self) -> None:
        calls: list[httpx.Request] = []
        http = _recorder(calls)
        self.addAsyncCleanup(http.aclose)
        client = ITMClient(http_client=http)

        await client.fundamentals.get("aapl", "income", timeframe="annual", limit=4)
        self.assertEqual(calls[0].url.path, "/v2/fundamentals/AAPL/income")
        self.assertEqual(calls[0].url.params["timeframe"], "annual")
        self.assertEqual(calls[0].url.params["limit"], "4")

    async def test_omitted_params_are_not_sent(self) -> None:
        calls: list[httpx.Request] = []
        http = _recorder(calls)
        self.addAsyncCleanup(http.aclose)
        client = ITMClient(http_client=http)

        await client.fundamentals.get("AAPL", "ratios")
        self.assertNotIn("timeframe", calls[0].url.params)
        self.assertNotIn("limit", calls[0].url.params)


class ConditionalReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_if_none_match_on_fundamentals_and_news(self) -> None:
        calls: list[httpx.Request] = []
        http = _recorder(calls, httpx.Response(304, headers={"etag": '"v1"'}))
        self.addAsyncCleanup(http.aclose)
        client = ITMClient(http_client=http)

        result = await client.fundamentals.get("AAPL", "ratios", if_none_match='"v1"')
        self.assertEqual(calls[0].headers["if-none-match"], '"v1"')
        self.assertEqual(result.status, 304)
        self.assertTrue(result.not_modified)

        await client.fundamentals.news(if_none_match='"n1"', symbols="SPY")
        self.assertEqual(calls[1].headers["if-none-match"], '"n1"')
        self.assertEqual(calls[1].url.params["symbols"], "SPY")
        # The conditional token is a header, never a query param.
        self.assertNotIn("if_none_match", calls[1].url.params)

        await client.fundamentals.digest(kind="brief", if_none_match='"d1"')
        self.assertEqual(calls[2].headers["if-none-match"], '"d1"')
        self.assertEqual(calls[2].url.params["kind"], "brief")

    async def test_unconditional_read_sends_no_header(self) -> None:
        calls: list[httpx.Request] = []
        http = _recorder(calls)
        self.addAsyncCleanup(http.aclose)
        client = ITMClient(http_client=http)

        await client.fundamentals.news()
        self.assertNotIn("if-none-match", calls[0].headers)


class PerCallTimeoutTests(unittest.IsolatedAsyncioTestCase):
    """Python's counterpart to the TypeScript per-call AbortSignal."""

    async def test_per_call_timeout_overrides_the_client_timeout(self) -> None:
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.extensions["timeout"])
            return httpx.Response(200, json={"data": [], "meta": {}})

        http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=30.0
        )
        self.addAsyncCleanup(http.aclose)
        client = ITMClient(http_client=http)

        await client.bars("SPY", from_=0, to=1, timeout=2.5)
        await client.bars("SPY", from_=0, to=1)
        self.assertEqual(seen[0]["read"], 2.5)
        self.assertEqual(seen[1]["read"], 30.0)

    async def test_the_facade_forwards_it(self) -> None:
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.extensions["timeout"])
            return httpx.Response(200, json={"data": [], "meta": {}})

        async with itm.AsyncITMClient(
            http_transport=httpx.MockTransport(handler), timeout=30.0
        ) as client:
            await client.get_bars("SPY", from_=0, to=1, timeout=1.25)
        self.assertEqual(seen[0]["read"], 1.25)


if __name__ == "__main__":
    unittest.main()
