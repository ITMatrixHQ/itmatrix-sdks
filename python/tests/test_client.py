import unittest

import httpx

from itmatrix._core import ITMClient, ITMError
from itmatrix._core._wire.rest_pb2 import BarsResponse


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_json_method_and_auth(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v2/gex/SPY/grid")
            self.assertEqual(request.url.params["top"], "5")
            self.assertEqual(request.headers["authorization"], "Bearer itm_test")
            self.assertEqual(request.headers["accept"], "application/json")
            return httpx.Response(
                200,
                json={
                    "data": {
                        "spot": 600,
                        "net_gex": 12,
                        "max_abs_gex": 9,
                        "strikes": [],
                    },
                    "meta": {"plane": "public"},
                },
                headers={"x-request-id": "req-json"},
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(http.aclose)
        client = ITMClient(api_key="itm_test", http_client=http)
        result = await client.gex("SPY", top=5)

        self.assertEqual(result.data["spot"], 600)
        self.assertEqual(result.meta["plane"], "public")
        self.assertEqual(result.request_id, "req-json")

    async def test_protobuf_negotiation_preserves_uint64(self) -> None:
        wire = BarsResponse(price_scale=10_000, cursor="next")
        wire.bars.add(ts_ms=5_000_000_000, o=1, h=2, l=1, c=2, v=9)

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["accept"], "application/x-protobuf")
            return httpx.Response(
                200,
                content=wire.SerializeToString(),
                headers={
                    "content-type": "application/x-protobuf",
                    "x-itm-plane": "public",
                    "x-itm-caps": '["bars"]',
                },
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(http.aclose)
        client = ITMClient(transport="protobuf", http_client=http)
        result = await client.bars(
            "SPY",
            from_="2026-08-29T13:30:00Z",
            to="2026-08-29T20:00:00Z",
        )

        self.assertIsInstance(result.data, BarsResponse)
        self.assertEqual(result.data.bars[0].ts_ms, 5_000_000_000)
        self.assertEqual(result.meta["caps"], ["bars"])

    async def test_stable_error_shape(self) -> None:
        http = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    429,
                    json={"error": {"code": "rate_limited", "message": "slow down"}},
                    headers={"retry-after": "3", "x-request-id": "req-error"},
                )
            )
        )
        self.addAsyncCleanup(http.aclose)
        client = ITMClient(http_client=http, retries=0)

        with self.assertRaises(ITMError) as raised:
            await client.bars("SPY", from_="2026-08-29", to="2026-08-30")
        self.assertEqual(raised.exception.status, 429)
        self.assertEqual(raised.exception.code, "rate_limited")
        self.assertEqual(raised.exception.request_id, "req-error")
        self.assertEqual(raised.exception.retry_after_seconds, 3)

    def test_only_json_and_protobuf_transports(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported transport"):
            ITMClient(transport="binary")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()


class ErrorSurfaceTests(unittest.TestCase):
    """Every documented field of ITMError must actually resolve.

    `message` was missing until 0.3.1: the constructor took it and handed it to
    `Exception`, which keeps the text in `args` only, so `e.message` raised
    AttributeError while `e.status`, `e.code` and `e.request_id` all worked.
    An error handler reaches for `message` first, and TypeScript's ITMError
    exposes it, so the two languages disagreed.
    """

    def test_every_field_resolves(self) -> None:
        err = ITMError(
            status=401,
            code="unauthenticated",
            message="missing Authorization header",
            details={"request_id": "01ABC"},
            request_id="01ABC",
            retry_after_seconds=1.5,
        )
        self.assertEqual(err.status, 401)
        self.assertEqual(err.code, "unauthenticated")
        self.assertEqual(err.message, "missing Authorization header")
        self.assertEqual(err.request_id, "01ABC")
        self.assertEqual(err.retry_after_seconds, 1.5)
        self.assertEqual(err.details, {"request_id": "01ABC"})
        # str() keeps working — the Exception contract is unchanged.
        self.assertEqual(str(err), "missing Authorization header")
