"""Small asynchronous REST client with no mutation retries."""

import asyncio
import json
import ssl
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

import aiohttp
from yarl import URL

from .models import OFFSET_LIMITS, Device, InvalidResponse, Zone, parse_zones

REQUEST_TIMEOUT = 10
MAX_RESPONSE_BYTES = 1024 * 1024


class ApiError(Exception):
    """A sanitized error safe for user-visible diagnostics."""

    def __init__(
        self,
        code: str,
        *,
        status: int | None = None,
        uncertain: bool = False,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.uncertain = uncertain
        self.retry_after = retry_after


def normalize_origin(value: str) -> str:
    """Accept only an explicit HTTP(S) origin, never credentials or API paths."""
    try:
        url = URL(value.strip())
        if (
            url.scheme not in ("http", "https")
            or not url.host
            or url.user is not None
            or url.password is not None
            or url.path not in ("", "/")
            or url.query_string
            or url.fragment
        ):
            raise ValueError
        _ = url.port
        return str(url.origin())
    except (TypeError, ValueError, UnicodeError) as err:
        raise ValueError("Enter an HTTP or HTTPS origin without credentials or a path") from err


def normalize_token(value: str) -> str:
    """Require an API token and reject unsafe header values locally."""
    if not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ApiError("invalid_auth")
    token = value.strip()
    if not token:
        raise ApiError("invalid_auth")
    return token


def _retry_after(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        seconds = int(value)
    except ValueError:
        try:
            seconds = int((parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
        except TypeError, ValueError, OverflowError:
            return None
    return max(0, min(300, seconds))


class ConnexClient:
    """Use a caller-owned session; never follow redirects or repeat requests."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        origin: str,
        token: str,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self._session = session
        self.origin = normalize_origin(origin)
        self._token = normalize_token(token)
        self._ssl_context = ssl_context

    async def _request(self, method: str, path: str, *, body: dict[str, Any] | None = None) -> Any:
        mutation = method != "GET"
        expected = 202 if mutation else 200
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self._token}"}
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.request(
                    method,
                    URL(f"{self.origin}/api/v1{path}", encoded=True),
                    json=body,
                    headers=headers,
                    allow_redirects=False,
                    ssl=self._ssl_context or True,
                ) as response:
                    if response.status != expected:
                        status = response.status
                        code = {
                            400: "invalid_value",
                            422: "invalid_value",
                            401: "invalid_auth",
                            403: "forbidden",
                            404: "not_found",
                            409: "conflict",
                            413: "incompatible",
                            415: "incompatible",
                            429: "rate_limited",
                            503: "unavailable",
                        }.get(status, "server_error" if status >= 500 else "incompatible")
                        raise ApiError(
                            code,
                            status=status,
                            # API v1 explicitly rejects 503 without saving settings.
                            uncertain=mutation
                            and (status < 400 or (status >= 500 and status != 503)),
                            retry_after=_retry_after(response.headers.get("Retry-After")),
                        )
                    # Acceptance is established by the status; don't wait for a body.
                    if mutation:
                        return None
                    if response.content_type != "application/json":
                        raise ApiError("incompatible")
                    chunks = bytearray()
                    async for chunk in response.content.iter_chunked(16384):
                        chunks.extend(chunk)
                        if len(chunks) > MAX_RESPONSE_BYTES:
                            raise ApiError("incompatible")
                    try:
                        return json.loads(chunks)
                    except (ValueError, UnicodeError, RecursionError) as err:
                        raise ApiError("incompatible") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            # Transport exception text can contain an address or credential: omit it.
            raise ApiError("cannot_connect", uncertain=mutation) from err

    async def get_device(self) -> Device:
        try:
            return Device.parse(await self._request("GET", "/device"))
        except InvalidResponse as err:
            raise ApiError("incompatible") from err

    async def get_zones(self) -> dict[str, Zone]:
        try:
            return parse_zones(await self._request("GET", "/zones"))
        except InvalidResponse as err:
            raise ApiError("incompatible") from err

    async def get_zone(self, zone_id: str) -> Zone:
        try:
            zone = Zone.parse(await self._request("GET", f"/zones/{quote(zone_id, safe='')}"))
            if zone.id != zone_id:
                raise InvalidResponse("Zone identity mismatch")
            return zone
        except InvalidResponse as err:
            raise ApiError("incompatible") from err

    async def patch_zone(self, zone_id: str, values: dict[str, Any]) -> None:
        if not values or set(values) - {"target_temperature", "mode", "eco_offset"}:
            raise ValueError("Unsupported control fields")
        body = dict(values)
        if "eco_offset" in body:
            # Firmware requires a JSON integer, while HA number actions use floats.
            # Validate before removing harmless unit-conversion floating-point residue.
            body["eco_offset"] = int(OFFSET_LIMITS.validate(body["eco_offset"]))
        await self._request("PATCH", f"/zones/{quote(zone_id, safe='')}", body=body)
