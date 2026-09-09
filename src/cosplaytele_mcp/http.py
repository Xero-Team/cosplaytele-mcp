from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlencode, urljoin, urlparse

import httpx
from selectolax.parser import HTMLParser

logger = logging.getLogger(__name__)

RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 0.4
RETRY_STATUSES = frozenset({429, 502, 503, 504})
HTTP_LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=10)
HTTP_TIMEOUT = httpx.Timeout(30.0, connect=15.0)
CACHE_TTL = 60.0
CACHE_SIZE = 128
MAX_REDIRECTS = 5
USE_CLIENT_TIMEOUT: Final = object()


class OutboundUrlError(httpx.RequestError):
    """A request attempted to leave the configured source-site allowlist."""


def describe_error(exc: BaseException) -> str:
    parts: list[str] = [type(exc).__name__]
    text = str(exc).strip()
    if text:
        parts.append(text)
    cause = exc.__cause__ or exc.__context__
    if cause is not None and cause is not exc:
        nested = type(cause).__name__
        nested_text = str(cause).strip()
        parts.append(f"{nested}: {nested_text}" if nested_text else nested)
    return ": ".join(parts)


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass
class _CacheEntry:
    expires: float
    status_code: int
    headers: list[tuple[str, str]]
    content: bytes
    url: str


class Http:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        cache_ttl: float = CACHE_TTL,
        cache_size: int = CACHE_SIZE,
        allowed_hosts: tuple[str, ...] = (),
    ) -> None:
        self.client = client
        self._cache_ttl = cache_ttl
        self._cache_size = cache_size
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._allowed_hosts = tuple(
            ((urlparse(host).hostname or host).lower().removeprefix("www."))
            for host in allowed_hosts
        )

    async def get(
        self,
        url: str,
        *,
        referer: str | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | httpx.Timeout | object | None = USE_CLIENT_TIMEOUT,
    ) -> httpx.Response:
        self._validate_url(url)
        key = _cache_key(url, params)
        cached = self._cache_take(key)
        if cached is not None:
            return cached
        headers = {"Referer": referer} if referer else None
        last_error: BaseException | None = None
        for attempt in range(RETRY_ATTEMPTS):
            try:
                response = await self._get_following_safe_redirects(
                    url, params=params, headers=headers, timeout=timeout
                )
                if response.status_code in RETRY_STATUSES and attempt < RETRY_ATTEMPTS - 1:
                    logger.debug("retryable HTTP %s for %s", response.status_code, url)
                    await asyncio.sleep(RETRY_BACKOFF * (attempt + 1))
                    continue
                response.raise_for_status()
                self._cache_put(key, response)
                return response
            except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
                last_error = exc
                if attempt >= RETRY_ATTEMPTS - 1:
                    raise
                logger.debug("retrying %s after %s", url, type(exc).__name__)
                await asyncio.sleep(RETRY_BACKOFF * (attempt + 1))
        raise last_error or RuntimeError(f"GET failed: {url}")

    async def _get_following_safe_redirects(
        self,
        url: str,
        *,
        params: dict[str, Any] | None,
        headers: dict[str, str] | None,
        timeout: float | httpx.Timeout | object | None,
    ) -> httpx.Response:
        current_url = url
        for redirect_count in range(MAX_REDIRECTS + 1):
            self._validate_url(current_url)
            request_args: dict[str, Any] = {"headers": headers, "follow_redirects": False}
            if redirect_count == 0:
                request_args["params"] = params
            # Omitting timeout is meaningfully different from timeout=None:
            # the former inherits AsyncClient's configured timeout.
            if timeout is not USE_CLIENT_TIMEOUT:
                request_args["timeout"] = timeout
            response = await self.client.get(current_url, **request_args)
            if not response.is_redirect:
                return response
            location = response.headers.get("location")
            if not location:
                return response
            if redirect_count == MAX_REDIRECTS:
                raise httpx.TooManyRedirects("Exceeded redirect limit", request=response.request)
            current_url = urljoin(str(response.url), location)
        raise AssertionError("unreachable")

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        try:
            port = parsed.port
        except ValueError as exc:
            raise OutboundUrlError(f"Invalid port in URL {url!r}", request=None) from exc
        if parsed.scheme not in {"http", "https"} or not host:
            raise OutboundUrlError("Only absolute http(s) URLs are allowed", request=None)
        if port not in {None, 80, 443}:
            raise OutboundUrlError(f"Port {port} is not allowed for {host}", request=None)
        if self._allowed_hosts and not any(
            host == allowed or host.endswith(f".{allowed}") for allowed in self._allowed_hosts
        ):
            raise OutboundUrlError(f"Host {host!r} is not an allowed source site", request=None)

    async def get_html(self, url: str, *, referer: str | None = None) -> HTMLParser:
        response = await self.get(url, referer=referer)
        return HTMLParser(response.text)

    async def get_json(
        self,
        url: str,
        *,
        referer: str | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | httpx.Timeout | object | None = USE_CLIENT_TIMEOUT,
    ) -> Any:
        response = await self.get(url, referer=referer, params=params, timeout=timeout)
        return response.json()

    def _cache_take(self, key: str) -> httpx.Response | None:
        if self._cache_ttl <= 0:
            return None
        entry = self._cache.get(key)
        if entry is None:
            return None
        if entry.expires <= time.monotonic():
            self._cache.pop(key, None)
            return None
        self._cache.move_to_end(key)
        return _response_from_cache(entry)

    def _cache_put(self, key: str, response: httpx.Response) -> None:
        if self._cache_ttl <= 0 or self._cache_size <= 0:
            return
        self._cache[key] = _CacheEntry(
            expires=time.monotonic() + self._cache_ttl,
            status_code=response.status_code,
            headers=list(response.headers.items()),
            content=response.content,
            url=str(response.url),
        )
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)


def _cache_key(url: str, params: dict[str, Any] | None) -> str:
    if not params:
        return url
    items = sorted((str(key), str(value)) for key, value in params.items())
    return f"{url}?{urlencode(items)}"


def _response_from_cache(entry: _CacheEntry) -> httpx.Response:
    return httpx.Response(
        entry.status_code,
        headers=entry.headers,
        content=entry.content,
        request=httpx.Request("GET", entry.url),
    )
