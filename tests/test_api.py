"""Actual HTTP serialization, failures and opaque path handling."""

import asyncio

import pytest
from aiohttp import ClientSession, web

from custom_components.open_connex_ha.api import (
    ApiError,
    ConnexClient,
    normalize_origin,
    normalize_token,
)
from custom_components.open_connex_ha.models import InvalidValue


@pytest.mark.parametrize(
    "value",
    [
        "host",
        "ftp://host",
        "http://user:pass@host",
        "http://host/api/v1",
        "http://host?token=x",
        "http://host/#x",
        "http://host:bad",
    ],
)
def test_reject_bad_origin(value):
    with pytest.raises(ValueError):
        normalize_origin(value)


def test_normalize_origin():
    assert normalize_origin("  HTTP://Example.COM:80/  ") == "http://example.com"
    assert normalize_origin("https://[::1]:8443/") == "https://[::1]:8443"


async def test_opaque_zone_path_and_empty_acceptance(controller):
    zone = controller.zones.pop("zone_test")
    zone["id"] = "zone/a ?#% ü"
    controller.zones[zone["id"]] = zone
    async with ClientSession() as session:
        api = ConnexClient(session, controller.origin, controller.token)
        assert (await api.get_zone(zone["id"])).id == zone["id"]
        await api.patch_zone(zone["id"], {"target_temperature": 20})
    assert controller.requests[-1][1].endswith("zone%2Fa%20%3F%23%25%20%C3%BC")
    assert controller.patches == [{"target_temperature": 20}]
    assert controller.authorization_headers == [f"Bearer {controller.token}"] * 2


@pytest.mark.parametrize("value", [-9.0, -1.0, 0.0, 1.0, 9.0, 0.9999999999999999])
async def test_offset_encoded_as_json_integer(controller, value):
    values = {"eco_offset": value}
    async with ClientSession() as session:
        await ConnexClient(session, controller.origin, controller.token).patch_zone(
            "zone_test", values
        )
    assert type(controller.patches[0]["eco_offset"]) is int
    assert controller.patches == [{"eco_offset": round(value)}]
    assert type(values["eco_offset"]) is float


@pytest.mark.parametrize("value", [-10, 10, 1.5, True, None, float("nan")])
async def test_invalid_offset_is_not_rounded_or_sent(controller, value):
    async with ClientSession() as session:
        with pytest.raises(InvalidValue):
            await ConnexClient(session, controller.origin, controller.token).patch_zone(
                "zone_test", {"eco_offset": value}
            )
    assert not controller.requests


async def test_token_is_trimmed_and_sent_on_every_request(controller):
    async with ClientSession() as session:
        api = ConnexClient(session, controller.origin, f"  {controller.token}  ")
        assert (await api.get_device()).id == controller.device["id"]
        assert "zone_test" in await api.get_zones()
        await api.patch_zone("zone_test", {"target_temperature": 20})
    assert controller.authorization_headers == [f"Bearer {controller.token}"] * 3
    assert controller.patches == [{"target_temperature": 20}]


@pytest.mark.parametrize(
    "token", [None, 12, "", "   ", "secret\n", "secret\r", "secret\x00", "secret\x7f"]
)
def test_invalid_tokens_rejected(token):
    with pytest.raises(ApiError, match="invalid_auth"):
        normalize_token(token)


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "invalid_value"),
        (422, "invalid_value"),
        (401, "invalid_auth"),
        (403, "forbidden"),
        (404, "not_found"),
        (409, "conflict"),
        (413, "incompatible"),
        (415, "incompatible"),
        (429, "rate_limited"),
        (503, "unavailable"),
        (500, "server_error"),
    ],
)
async def test_http_errors_are_safe_and_not_retried(controller, status, code):
    controller.faults[("PATCH", "/api/v1/zones/zone_test")].append(
        web.Response(
            status=status,
            text=f"malformed secret {controller.token}",
            headers={"Retry-After": "99999"},
        )
    )
    async with ClientSession() as session:
        api = ConnexClient(session, controller.origin, controller.token)
        with pytest.raises(ApiError) as raised:
            await api.patch_zone("zone_test", {"mode": "eco"})
    assert raised.value.code == code
    assert raised.value.retry_after == 300
    assert raised.value.uncertain is (status == 500)
    assert controller.token not in str(raised.value)
    assert len(controller.requests) == 1


async def test_redirects_are_rejected(controller):
    controller.faults[("GET", "/api/v1/device")].append(
        web.Response(status=302, headers={"Location": "/api/v1/zones"})
    )
    async with ClientSession() as session:
        with pytest.raises(ApiError):
            await ConnexClient(session, controller.origin, controller.token).get_device()
    assert len(controller.requests) == 1


@pytest.mark.parametrize(
    "response",
    [
        web.Response(text="{", content_type="application/json"),
        web.Response(text="{}", content_type="text/html"),
        web.json_response({}),
        web.json_response({"id": "x", "firmware_version": "1", "health": "unknown"}),
    ],
)
async def test_malformed_responses(controller, response):
    controller.faults[("GET", "/api/v1/device")].append(response)
    async with ClientSession() as session:
        with pytest.raises(ApiError, match="incompatible"):
            await ConnexClient(session, controller.origin, controller.token).get_device()


async def test_oversize_response(controller, monkeypatch):
    monkeypatch.setattr("custom_components.open_connex_ha.api.MAX_RESPONSE_BYTES", 20)
    async with ClientSession() as session:
        with pytest.raises(ApiError, match="incompatible"):
            await ConnexClient(session, controller.origin, controller.token).get_device()


async def test_timeout_does_not_retry(controller, monkeypatch):
    monkeypatch.setattr("custom_components.open_connex_ha.api.REQUEST_TIMEOUT", 0.02)
    release = asyncio.Event()

    async def slow(request):
        await release.wait()
        return web.Response(status=202)

    controller.faults[("PATCH", "/api/v1/zones/zone_test")].append(slow)
    try:
        async with ClientSession() as session:
            with pytest.raises(ApiError) as raised:
                await ConnexClient(session, controller.origin, controller.token).patch_zone(
                    "zone_test", {"mode": "eco"}
                )
            assert raised.value.uncertain
            assert not session.closed
        assert len(controller.requests) == 1
    finally:
        release.set()
