"""Configuration and recovery must use GETs and preserve identity."""

from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType, InvalidData

from custom_components.open_connex_ha.const import DOMAIN


async def test_user_setup_and_duplicate(hass, controller):
    data = {"origin": controller.origin, "token": controller.token}
    with patch(
        "custom_components.open_connex_ha.async_setup_entry",
        new=AsyncMock(return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["description_placeholders"] == {"example_address": "http://open-connex.local"}
        result = await hass.config_entries.flow.async_configure(result["flow_id"], data)
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["result"].unique_id == controller.device["id"]
        await hass.async_block_till_done()
        duplicate = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}, data=data
        )
        assert duplicate["reason"] == "already_configured"
    assert all(method == "GET" for method, _, _ in controller.requests)


@pytest.mark.parametrize("credentials", [{}, {"token": ""}, {"token": "   "}])
async def test_setup_requires_token_locally(hass, controller, credentials):
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={"origin": controller.origin, **credentials},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert not controller.requests


@pytest.mark.parametrize(
    ("status", "code"), [(401, "invalid_auth"), (403, "forbidden"), (500, "cannot_connect")]
)
async def test_flow_errors(hass, controller, status, code):
    controller.faults[("GET", "/api/v1/device")].append(web.Response(status=status))
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={"origin": controller.origin, "token": controller.token},
    )
    assert result["errors"] == {"base": code}


@pytest.mark.parametrize(
    ("extra", "code"),
    [
        ({"origin": "not an origin"}, "invalid_origin"),
        ({"token": "\nsecret"}, "invalid_auth"),
        ({"ca_certificate": "not PEM"}, "invalid_certificate"),
    ],
)
async def test_invalid_setup_is_local(hass, controller, extra, code):
    data = {"origin": controller.origin, "token": controller.token, **extra}
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}, data=data
    )
    assert result["errors"] == {"base": code}
    assert not controller.requests


@pytest.mark.parametrize(
    "source", [config_entries.SOURCE_REAUTH, config_entries.SOURCE_RECONFIGURE]
)
async def test_recovery_preserves_entry(hass, controller, entry, source):
    entry.add_to_hass(hass)
    with patch("homeassistant.config_entries.ConfigEntries.async_reload", return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": source, "entry_id": entry.entry_id, "unique_id": entry.unique_id},
            data=dict(entry.data) if source == config_entries.SOURCE_REAUTH else None,
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], dict(entry.data))
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] in ("reauth_successful", "reconfigure_successful")
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    assert entry.unique_id == controller.device["id"]


@pytest.mark.parametrize("credentials", [{}, {"token": ""}])
@pytest.mark.parametrize(
    "source", [config_entries.SOURCE_REAUTH, config_entries.SOURCE_RECONFIGURE]
)
async def test_recovery_cannot_clear_saved_token(hass, controller, entry, credentials, source):
    entry.add_to_hass(hass)
    with patch("homeassistant.config_entries.ConfigEntries.async_reload", return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": source, "entry_id": entry.entry_id, "unique_id": entry.unique_id},
            data=dict(entry.data) if source == config_entries.SOURCE_REAUTH else None,
        )
        data = {"origin": controller.origin, **credentials}
        if "token" not in credentials:
            with pytest.raises(InvalidData, match="token"):
                await hass.config_entries.flow.async_configure(result["flow_id"], data)
        else:
            result = await hass.config_entries.flow.async_configure(result["flow_id"], data)
            assert result["type"] == FlowResultType.FORM
            assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data["token"] == controller.token
    assert entry.unique_id == controller.device["id"]
    assert not controller.requests


async def test_reconfigure_rejects_replacement(hass, controller, entry):
    entry.add_to_hass(hass)
    controller.device["id"] = "different"
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
            "unique_id": entry.unique_id,
        },
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], dict(entry.data))
    assert result["reason"] == "unique_id_mismatch"
    assert entry.unique_id == "controller_test"
