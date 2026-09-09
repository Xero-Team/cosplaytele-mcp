from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from selectolax.parser import HTMLParser

logger = logging.getLogger(__name__)

RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 0.4
RETRY_STATUSES = frozenset({429, 502, 503, 504})
HTTP_LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=10)
HTTP_TIMEOUT = httpx.Timeout(30.0, connect=15.0)


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


class Http:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def get(
        self,
        url: str,
        *,
        referer: str | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        headers = {"Referer": referer} if referer else None
        last_error: BaseException | None = None
        for attempt in range(RETRY_ATTEMPTS):
            try:
                response = await self.client.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=timeout,
                )
                if response.status_code in RETRY_STATUSES and attempt < RETRY_ATTEMPTS - 1:
                    logger.debug("retryable HTTP %s for %s", response.status_code, url)
                    await asyncio.sleep(RETRY_BACKOFF * (attempt + 1))
                    continue
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
                last_error = exc
                if attempt >= RETRY_ATTEMPTS - 1:
                    raise
                logger.debug("retrying %s after %s", url, type(exc).__name__)
                await asyncio.sleep(RETRY_BACKOFF * (attempt + 1))
        raise last_error or RuntimeError(f"GET failed: {url}")

    async def get_html(self, url: str, *, referer: str | None = None) -> HTMLParser:
        response = await self.get(url, referer=referer)
        return HTMLParser(response.text)

    async def get_json(
        self,
        url: str,
        *,
        referer: str | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        response = await self.get(url, referer=referer, params=params, timeout=timeout)
        return response.json()
