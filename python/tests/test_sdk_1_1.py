"""1.1.0 surface: dark-pool levels, bars ``adj``, fundamentals vintages,
``Result.raw``, the ``X-ITM-SDK`` header and the upgrade notice."""

import os
import unittest
import warnings
from collections.abc import Callable
from unittest import mock

import httpx

import itmatrix as itm
from itmatrix._core import ITMClient
from itmatrix._core import version as sdk_version
from itmatrix._core._wire.rest_pb2 import GexGridResponse

LEVEL = {
    "ticker": "NVDA",
    "date": "2026-11-05",
    "price_thousandths": 191250,
    "volume": 1200.0,
    "notional": 229500.0,
    "trade_count": 7,
}


def envelope(data: object, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(200, json={"data": data, "meta": {}}, headers=headers or {})


class Recorder:
    def __init__(self, respond: Callable[[httpx.Request], httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self._respond = respond

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._respond(request)


def core(recorder: Recorder, **options: object) -> ITMClient:
    return ITMClient(http_transport=httpx.MockTransport(recorder), **options)


class LevelsTests(unittest.IsolatedAsyncioTestCase):
    async def test_levels_reads_the_keyed_route_with_only_its_params(self) -> None:
        recorder = Recorder(lambda _r: envelope([LEVEL]))
        client = core(recorder, transport="protobuf")
        self.addAsyncCleanup(client.aclose)
        result = await client.offexchange.levels(
            "nvda", from_="2026-11-01", to="2026-11-05", tz="UTC", limit=50, cursor="c1"
        )
        (request,) = recorder.requests
        self.assertEqual(request.url.path, "/v2/darkpool/NVDA/levels")
        self.assertEqual(
            dict(request.url.params),
            {"from": "2026-11-01", "to": "2026-11-05", "tz": "UTC", "limit": "50", "cursor": "c1"},
        )
        # JSON-only route: never negotiates protobuf, even on a protobuf client.
        self.assertEqual(request.headers["accept"], "application/json")
        self.assertEqual(result.data, [LEVEL])
        self.assertIs(client.darkpool.levels.__func__, client.offexchange.levels.__func__)

    async def test_levels_without_a_range_sends_no_params(self) -> None:
        recorder = Recorder(lambda _r: envelope([]))
        client = core(recorder)
        self.addAsyncCleanup(client.aclose)
        await client.darkpool.levels("SPY")
        self.assertEqual(dict(recorder.requests[0].url.params), {})

    def test_blocking_client_exposes_levels(self) -> None:
        recorder = Recorder(lambda _r: envelope([LEVEL]))
        with itm.ITMClient(api_key="itm_x", http_transport=httpx.MockTransport(recorder)) as client:
            result = client.offexchange.levels("NVDA", limit=1)
        self.assertEqual(result.data[0]["price_thousandths"], 191250)


class ParameterTests(unittest.IsolatedAsyncioTestCase):
    async def test_bars_adj_is_sent(self) -> None:
        recorder = Recorder(lambda _r: envelope([]))
        async with itm.AsyncITMClient(http_transport=httpx.MockTransport(recorder)) as client:
            await client.get_bars("aapl", from_="2026-11-03", to="2026-11-04", adj="none")
            await client.get_bars_for_period("AAPL", "today", adj="split")
        self.assertEqual(recorder.requests[0].url.params["adj"], "none")
        self.assertEqual(recorder.requests[1].url.params["adj"], "split")

    async def test_bars_omit_adj_by_default(self) -> None:
        recorder = Recorder(lambda _r: envelope([]))
        async with itm.AsyncITMClient(http_transport=httpx.MockTransport(recorder)) as client:
            await client.get_bars("AAPL", from_="2026-11-03", to="2026-11-04")
        self.assertNotIn("adj", recorder.requests[0].url.params)

    async def test_fundamentals_as_reported_vintage(self) -> None:
        recorder = Recorder(lambda _r: envelope([]))
        client = core(recorder)
        self.addAsyncCleanup(client.aclose)
        await client.fundamentals.get(
            "msft", "income", vintage="as_reported", as_of="2025-06-30"
        )
        params = recorder.requests[0].url.params
        self.assertEqual(recorder.requests[0].url.path, "/v2/fundamentals/MSFT/income")
        self.assertEqual((params["vintage"], params["as_of"]), ("as_reported", "2025-06-30"))

    async def test_as_of_without_as_reported_is_refused_before_a_request(self) -> None:
        recorder = Recorder(lambda _r: envelope([]))
        client = core(recorder)
        self.addAsyncCleanup(client.aclose)
        for vintage in (None, "latest"):
            with self.assertRaisesRegex(ValueError, "as_reported"):
                await client.fundamentals.get(
                    "MSFT", "income", vintage=vintage, as_of="2025-06-30"
                )
        self.assertEqual(recorder.requests, [])


class RawTests(unittest.IsolatedAsyncioTestCase):
    async def test_raw_keeps_fields_the_typed_model_drops(self) -> None:
        grid = {
            "spot": 600.0,
            "net_gex": 1.0,
            "max_abs_gex": 1.0,
            "strikes": [{"strike": 600000, "gex": 1.0, "added_later": 3}],
            "added_later": "kept",
        }
        recorder = Recorder(lambda _r: envelope(grid))
        async with itm.AsyncITMClient(http_transport=httpx.MockTransport(recorder)) as client:
            result = await client.get_gex("SPY")
        self.assertIsInstance(result.data, itm.GexGrid)
        self.assertEqual(result.raw["added_later"], "kept")
        self.assertEqual(result.raw["strikes"][0]["added_later"], 3)

    async def test_raw_is_the_protobuf_message_on_the_binary_transport(self) -> None:
        body = GexGridResponse(net_gex=2.0, max_abs_gex=2.0).SerializeToString()
        recorder = Recorder(
            lambda _r: httpx.Response(
                200, content=body, headers={"content-type": "application/x-protobuf"}
            )
        )
        async with itm.AsyncITMClient(
            transport="protobuf", http_transport=httpx.MockTransport(recorder)
        ) as client:
            result = await client.get_gex("SPY")
        self.assertIsInstance(result.raw, GexGridResponse)
        self.assertEqual(result.data.net_gex, 2.0)

    def test_raw_is_not_part_of_equality(self) -> None:
        self.assertEqual(
            itm.Result([1], {}, 200, raw={"x": 1}), itm.Result([1], {}, 200, raw=None)
        )


class VersionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        sdk_version._reset_for_tests()
        self.addCleanup(sdk_version._reset_for_tests)
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(sdk_version.SILENCE_ENV, None)

    async def calls(self, current: str | None, count: int = 2) -> list[warnings.WarningMessage]:
        headers = {} if current is None else {"X-ITM-SDK-Current": current}
        recorder = Recorder(lambda _r: envelope([], headers))
        client = core(recorder)
        self.addAsyncCleanup(client.aclose)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            for _ in range(count):
                await client.request("/v2/symbols")
        self.recorder = recorder
        return [w for w in caught if issubclass(w.category, itm.ITMUpgradeWarning)]

    async def test_every_request_declares_the_sdk(self) -> None:
        await self.calls(None, count=1)
        header = self.recorder.requests[0].headers["x-itm-sdk"]
        self.assertEqual(header, sdk_version.client_name())
        self.assertRegex(header, r"^itmatrix-python/\d+\.\d+\.\d+")

    async def test_a_newer_minor_warns_once_per_process(self) -> None:
        caught = await self.calls("9.4.0")
        self.assertEqual(len(caught), 1)
        self.assertIn("pip install -U itmatrix", str(caught[0].message))
        self.assertIn("9.4.0", str(caught[0].message))
        # A second client in the same process stays quiet.
        self.assertEqual(await self.calls("9.4.0"), [])

    async def test_same_minor_patch_or_prerelease_does_not_warn(self) -> None:
        major, minor = sdk_version.sdk_version().split(".")[:2]
        for advertised in (f"{major}.{minor}.0", f"{major}.{minor}.9", f"{major}.{minor}.0rc3"):
            self.assertEqual(await self.calls(advertised), [], advertised)

    async def test_older_or_garbled_advertisements_do_not_warn(self) -> None:
        for advertised in ("0.9.1", "latest", "", "  "):
            self.assertEqual(await self.calls(advertised), [], advertised)

    async def test_environment_silences_the_notice(self) -> None:
        os.environ[sdk_version.SILENCE_ENV] = "1"
        self.assertEqual(await self.calls("9.4.0"), [])

    async def test_the_notice_also_rides_error_responses(self) -> None:
        recorder = Recorder(
            lambda _r: httpx.Response(
                403,
                json={"error": {"code": "not_entitled_tier", "message": "no"}},
                headers={"X-ITM-SDK-Current": "9.4.0"},
            )
        )
        client = core(recorder)
        self.addAsyncCleanup(client.aclose)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with self.assertRaises(itm.ITMError):
                await client.request("/v2/symbols")
        self.assertEqual(
            [w.category for w in caught if issubclass(w.category, itm.ITMUpgradeWarning)],
            [itm.ITMUpgradeWarning],
        )


if __name__ == "__main__":
    unittest.main()
