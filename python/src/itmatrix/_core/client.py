"""Direct async client for the ITMatrixHQ /v2 API."""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import warnings
import weakref
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, Literal, TypeVar, cast
from urllib.parse import quote, unquote, urlsplit

import httpx
from google.protobuf.message import Message
from typing_extensions import Self

from ._wire.rest_pb2 import BarsResponse, ChainResponse, GexGridResponse, ReplayResponse
from .config import DEFAULT_BASE_URL
from .errors import ITMError
from .models import Credential, GexReferenceData, QueryValue, Result, Transport
from .resources import (
    AccountResource,
    DarkpoolResource,
    EconomyResource,
    FlowResource,
    FundamentalsResource,
    InfoResource,
    JournalResource,
    MarketResource,
    ProtocolResource,
    ReferenceResource,
    ScreenerResource,
    VolResource,
    WatchlistsResource,
)
from .stream import Stream

M = TypeVar("M", bound=Message)

# The only paths outside /v2/ this client will request. Liveness and readiness
# are served at the root and are unauthenticated, so InfoResource needs them;
# everything else stays under /v2/ so a caller cannot wander off the documented
# surface by passing a path to request(). An allowlist rather than a loosened
# prefix check: adding a path here should be a deliberate decision.
_ROOT_PATHS = frozenset({"/healthz", "/readyz"})

_CLOSED = "client is closed; create a new client to make more requests"


def _warn_unclosed(name: str) -> None:
    warnings.warn(
        f"{name} was garbage-collected without being closed; "
        "await client.aclose() or use `async with`",
        ResourceWarning,
        stacklevel=2,
    )


class ITMClient:
    """One readable entry point; generated protobuf stays behind _wire."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        transport: Transport = "json",
        api_key: Credential | None = None,
        token: Credential | None = None,
        http_client: httpx.AsyncClient | None = None,
        retries: int = 2,
        timeout: float = 30.0,
        http_transport: httpx.AsyncBaseTransport | None = None,
        stream_url: str | None = None,
        stream_connect: Any = None,
        stream_max_queue: int = 64,
    ) -> None:
        if transport not in {"json", "protobuf"}:
            raise ValueError(f"unsupported transport {transport!r}")
        if retries < 0:
            raise ValueError("retries must be non-negative")
        if api_key is not None and token is not None:
            raise ValueError("configure api_key or token, not both")

        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError(
                "base_url must be an http(s) origin without credentials, path, query or fragment"
            )
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        if http_client is not None and http_transport is not None:
            raise ValueError("configure http_client or http_transport, not both")
        self.base_url = base_url.rstrip("/")
        self.transport = transport
        self._credential = api_key if api_key is not None else token
        self._http = http_client or httpx.AsyncClient(
            timeout=timeout, transport=http_transport, follow_redirects=False
        )
        self._closed = False
        self._owns_http = http_client is None
        self._retries = retries
        # Async cleanup cannot run from a finalizer, so an unclosed client
        # that owns its pool only warns when it is collected.
        self._finalizer = weakref.finalize(self, _warn_unclosed, type(self).__name__)
        self._finalizer.atexit = False
        if not self._owns_http:
            self._finalizer.detach()

        self.account = AccountResource(self.request)
        self.darkpool = DarkpoolResource(self.request)
        self.offexchange = self.darkpool
        self.economy = EconomyResource(self.request)
        self.flow = FlowResource(self.request)
        self.fundamentals = FundamentalsResource(self.request)
        self.info = InfoResource(self.request)
        self.journal = JournalResource(self.request)
        self.market = MarketResource(self.request)
        self.protocols = ProtocolResource(self.request)
        self.reference = ReferenceResource(self.request)
        self.screener = ScreenerResource(self.request)
        self.vol = VolResource(self.request)
        self.watchlists = WatchlistsResource(self.request)

        self.stream = Stream(
            url=stream_url or _websocket_url(self.base_url),
            ticket=self._ticket,
            connect=stream_connect,
            max_queue=stream_max_queue,
        )

    async def __aenter__(self) -> Self:
        if self._closed:
            raise RuntimeError(_CLOSED)
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the stream and the owned HTTP pool. Idempotent.

        ``async with`` calls this for you; without it, await ``aclose()``
        when you are done. A request after it raises ``RuntimeError``.
        """
        if self._closed:
            return
        self._closed = True
        self._finalizer.detach()
        try:
            await self.stream.close()
        finally:
            if self._owns_http:
                await self._http.aclose()

    async def gex(
        self,
        symbol: str,
        *,
        at: str | int | datetime | None = None,
        tz: str | None = None,
        expiries: str | list[str] | tuple[str, ...] | None = None,
        dte: int | None = None,
        top: int | None = None,
        by_expiry: bool | None = None,
        timeout: float | None = None,
    ) -> Result[dict[str, Any] | GexGridResponse]:
        return await self._wire(
            f"/v2/gex/{_symbol(symbol)}/grid",
            {
                "at": at,
                "tz": tz,
                "expiries": expiries,
                "dte": dte,
                "top": top,
                "by_expiry": by_expiry,
            },
            GexGridResponse,
            timeout=timeout,
        )

    async def gex_reference(
        self,
        symbol: str,
        *,
        date: str,
        basis: Literal["open", "prev_close"],
    ) -> Result[GexReferenceData]:
        """Get the exact open or preceding-close persisted GEX book.

        An unavailable book is a successful response with ``captured_at=None``
        and an empty ``strikes`` list; the server never substitutes a nearby
        session or treats missing strikes as zero.
        """
        return await self.request(
            f"/v2/gex/{_symbol(symbol)}/reference",
            query={"date": date, "basis": basis},
        )

    async def bars(
        self,
        symbol: str,
        *,
        from_: str | int | datetime,
        to: str | int | datetime,
        timeframe: str | None = None,
        tz: str | None = None,
        source: Literal["trade", "mid"] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        timeout: float | None = None,
    ) -> Result[list[dict[str, Any]] | BarsResponse]:
        return await self._wire(
            f"/v2/stocks/{_symbol(symbol)}/bars",
            {
                "from": from_,
                "to": to,
                "timeframe": timeframe,
                "tz": tz,
                "source": source,
                "limit": limit,
                "cursor": cursor,
            },
            BarsResponse,
            timeout=timeout,
        )

    async def replay(
        self,
        symbol: str,
        *,
        date: str,
        from_: str | None = None,
        to: str | None = None,
        tz: str | None = None,
        timeout: float | None = None,
    ) -> Result[dict[str, Any] | ReplayResponse]:
        return await self._wire(
            f"/v2/stocks/{_symbol(symbol)}/replay",
            {"date": date, "from": from_, "to": to, "tz": tz},
            ReplayResponse,
            timeout=timeout,
        )

    async def chain(
        self,
        symbol: str,
        *,
        at: str | int | datetime | None = None,
        tz: str | None = None,
        expiry: str | None = None,
        strike_gte: int | None = None,
        strike_lte: int | None = None,
        timeout: float | None = None,
    ) -> Result[dict[str, Any] | ChainResponse]:
        query = _chain_query(at, tz, expiry, strike_gte, strike_lte)
        return await self._wire(
            f"/v2/chain/{_symbol(symbol)}",
            query,
            ChainResponse,
            timeout=timeout,
        )

    async def expirations(
        self,
        symbol: str,
        *,
        at: str | int | datetime | None = None,
        tz: str | None = None,
        expiry: str | None = None,
        strike_gte: int | None = None,
        strike_lte: int | None = None,
        timeout: float | None = None,
    ) -> Result[list[str]]:
        return cast(
            Result[list[str]],
            await self._send(
                f"/v2/chain/{_symbol(symbol)}/expirations",
                query=_chain_query(at, tz, expiry, strike_gte, strike_lte),
                force_json=True,
                timeout=timeout,
            ),
        )

    async def request(
        self,
        path: str,
        *,
        method: str = "GET",
        query: Mapping[str, QueryValue] | None = None,
        body: Any = None,
        content_type: str = "application/json",
        accept: str | None = None,
        if_none_match: str | None = None,
        timeout: float | None = None,
        message_type: type[M] | None = None,
    ) -> Result[Any]:
        """Authenticated request to a relative ``/v2/`` path.

        ``timeout`` overrides the client-wide timeout for this call only.
        There is no separate cancellation token: an ``asyncio`` task that is
        cancelled cancels the request with it, which is the language's own
        mechanism and needs no SDK surface.
        """
        return await self._send(
            path,
            method=method,
            query=query,
            body=body,
            content_type=content_type,
            accept=accept,
            if_none_match=if_none_match,
            message_type=message_type,
            force_json=message_type is None,
            timeout=timeout,
        )

    async def _ticket(self) -> Mapping[str, Any]:
        result = await self._send("/v2/stream/ticket", method="POST", force_json=True)
        return cast(Mapping[str, Any], result.data)

    async def _wire(
        self,
        path: str,
        query: Mapping[str, QueryValue],
        message_type: type[M],
        timeout: float | None = None,
    ) -> Result[Any]:
        return await self._send(
            path, query=query, message_type=message_type, timeout=timeout
        )

    async def _send(
        self,
        path: str,
        *,
        method: str = "GET",
        query: Mapping[str, QueryValue] | None = None,
        body: Any = None,
        content_type: str = "application/json",
        accept: str | None = None,
        if_none_match: str | None = None,
        message_type: type[M] | None = None,
        force_json: bool = False,
        timeout: float | None = None,
    ) -> Result[Any]:
        protobuf = (
            not force_json and self.transport == "protobuf" and message_type is not None
        )
        if self._closed:
            raise RuntimeError(_CLOSED)
        decoded = unquote(path)
        parsed = urlsplit(path)
        if (
            (not path.startswith("/v2/") and path not in _ROOT_PATHS)
            or parsed.scheme
            or parsed.netloc
            or parsed.query
            or parsed.fragment
            or "\\" in decoded
            or any(p in {".", ".."} for p in decoded.split("/"))
        ):
            raise ValueError(
                "path must be a relative /v2/ API path without traversal, query or fragment"
            )
        method = method.upper()
        url = self.base_url + path
        params = {
            key: _query_value(value)
            for key, value in (query or {}).items()
            if value is not None
        }

        for attempt in range(self._retries + 1):
            headers = {
                "Accept": accept
                or ("application/x-protobuf" if protobuf else "application/json")
            }
            credential = await _credential(self._credential)
            if credential:
                headers["Authorization"] = f"Bearer {credential}"
            if if_none_match:
                headers["If-None-Match"] = if_none_match
            response = await _request(
                self._http,
                method,
                url,
                params=params,
                headers=headers,
                body=body,
                content_type=content_type,
                timeout=timeout,
            )
            if (
                method == "GET"
                and attempt < self._retries
                and response.status_code in {429, 502, 503, 504}
            ):
                retry_after = _number(response.headers.get("retry-after"))
                await asyncio.sleep(
                    min(retry_after, 30.0)
                    if retry_after and math.isfinite(retry_after) and retry_after > 0
                    else min(0.25 * (2**attempt), 2.0)
                )
                continue

            request_id = response.headers.get("x-request-id")
            etag = response.headers.get("etag")
            if response.status_code == 304:
                return Result(None, {}, 304, request_id, etag, True)
            if not response.is_success:
                raise _response_error(response)

            response_type = response.headers.get("content-type", "").lower()
            if response_type.startswith("application/x-protobuf"):
                if message_type is None:
                    raise TypeError("protobuf response received without a decoder")
                return Result(
                    message_type.FromString(response.content),
                    _binary_meta(response.headers),
                    response.status_code,
                    request_id,
                    etag,
                )
            if "json" not in response_type:
                return Result(
                    response.text,
                    {},
                    response.status_code,
                    request_id,
                    etag,
                )

            value = response.json()
            if isinstance(value, dict) and "data" in value:
                meta = value.get("meta")
                return Result(
                    value["data"],
                    meta if isinstance(meta, dict) else {},
                    response.status_code,
                    request_id,
                    etag,
                )
            return Result(value, {}, response.status_code, request_id, etag)
        raise AssertionError("unreachable")


async def _request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    params: Mapping[str, str | int | float],
    headers: Mapping[str, str],
    body: Any,
    content_type: str,
    timeout: float | None = None,
) -> httpx.Response:
    # httpx treats an omitted `timeout` as "use the client's"; passing one
    # overrides it for this call only. Never widened silently — a caller who
    # wants longer than the client allows says so here.
    extra: dict[str, Any] = {} if timeout is None else {"timeout": timeout}
    if body is None:
        return await client.request(
            method, url, params=params, headers=headers, follow_redirects=False, **extra
        )
    if content_type != "application/json":
        if not isinstance(body, str | bytes):
            raise TypeError("non-JSON request bodies must be strings or bytes")
        return await client.request(
            method,
            url,
            params=params,
            headers={**headers, "Content-Type": content_type},
            content=body,
            follow_redirects=False,
            **extra,
        )
    return await client.request(
        method,
        url,
        params=params,
        headers=headers,
        json=body,
        follow_redirects=False,
        **extra,
    )


def _chain_query(
    at: str | int | datetime | None,
    tz: str | None,
    expiry: str | None,
    strike_gte: int | None,
    strike_lte: int | None,
) -> Mapping[str, QueryValue]:
    return {
        "at": at,
        "tz": tz,
        "expiry": expiry,
        "strike_gte": strike_gte,
        "strike_lte": strike_lte,
    }


async def _credential(value: Credential | None) -> str | None:
    if value is None:
        return None
    result = value() if callable(value) else value
    return await result if inspect.isawaitable(result) else result


def _query_value(value: QueryValue) -> str | int | float:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, str | int | float):
        return value
    return ",".join(value)


def _response_error(response: httpx.Response) -> ITMError:
    try:
        value = response.json()
    except ValueError:
        value = {}
    error = value.get("error", {}) if isinstance(value, dict) else {}
    if not isinstance(error, dict):
        error = {}
    details = error.get("details")
    return ITMError(
        status=response.status_code,
        code=str(error.get("code", "unknown")),
        message=str(error.get("message", response.reason_phrase or "request failed")),
        details=details if isinstance(details, dict) else None,
        request_id=response.headers.get("x-request-id"),
        retry_after_seconds=_number(response.headers.get("retry-after")),
    )


def _binary_meta(headers: httpx.Headers) -> Mapping[str, Any]:
    extra = _header_json(headers.get("x-itm-extra"))
    meta = dict(extra) if isinstance(extra, dict) else {}
    plane = headers.get("x-itm-plane")
    caps = _header_json(headers.get("x-itm-caps"))
    if plane is not None:
        meta["plane"] = plane
    if caps is not None:
        meta["caps"] = caps
    return meta


def _header_json(value: str | None) -> Any:
    try:
        return json.loads(value) if value is not None else None
    except json.JSONDecodeError:
        return None


def _number(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _symbol(value: str) -> str:
    return quote(value.upper(), safe="")


def _websocket_url(base_url: str) -> str:
    if base_url.startswith("https://"):
        return "wss://" + base_url.removeprefix("https://") + "/v2/ws"
    if base_url.startswith("http://"):
        return "ws://" + base_url.removeprefix("http://") + "/v2/ws"
    raise ValueError("base_url must use http or https")
