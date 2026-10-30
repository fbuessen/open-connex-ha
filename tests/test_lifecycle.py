"""Polling, restart, metadata, and cleanup edge cases."""

from copy import deepcopy
from datetime import timedelta

import pytest
from aiohttp import web
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.open_connex_ha import async_remove_config_entry_device
from custom_components.open_connex_ha.const import DOMAIN
from custom_components.open_connex_ha.entity import entity_identifier

from .conftest import ZONE
from .test_integration import entity_id


async def test_poll_backoff_and_recovery(hass, controller, integration):
    coordinator = integration.runtime_data.coordinator
    for expected in [30, 60, 120, 240, 300, 300]:
        controller.faults[("GET", "/api/v1/device")].append(web.Response(status=503))
        await coordinator.async_refresh()
        assert coordinator.update_interval.total_seconds() == expected
        assert hass.states.get(entity_id(hass, "climate")).state == "unavailable"
    await coordinator.async_refresh()
    assert coordinator.update_interval.total_seconds() == 30
    assert hass.states.get(entity_id(hass, "climate")).state == "heat"


async def test_retry_after(hass, controller, integration):
    controller.faults[("GET", "/api/v1/device")].append(
        web.Response(status=429, headers={"Retry-After": "123"})
    )
    await integration.runtime_data.coordinator.async_refresh()
    assert integration.runtime_data.coordinator.update_interval.total_seconds() == 123


async def test_all_entities_disabled_still_discovers(hass, controller, entry):
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for domain, zone_id, kind in [
        ("sensor", None, "health"),
        ("climate", "zone_test", "climate"),
        ("number", "zone_test", "eco_offset"),
    ]:
        registry.async_get_or_create(
            domain,
            DOMAIN,
            entity_identifier(entry.unique_id, zone_id, kind),
            config_entry=entry,
            disabled_by=er.RegistryEntryDisabler.USER,
        )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert not hass.states.async_all("climate")
    new = deepcopy(ZONE)
    new["id"] = "new_zone"
    controller.zones["new_zone"] = new
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=40))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(hass.states.async_all("climate")) == 1
    await hass.config_entries.async_unload(entry.entry_id)


async def test_removal_restart_and_reappearance(hass, controller, integration):
    coordinator = integration.runtime_data.coordinator
    climate = entity_id(hass, "climate")
    registry = er.async_get(hass)
    original = registry.async_get(climate)
    controller.zones.clear()
    await coordinator.async_refresh()
    assert await async_remove_config_entry_device(
        hass, integration, dr.async_get(hass).async_get(original.device_id)
    )
    assert await hass.config_entries.async_reload(integration.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(climate).unique_id == original.unique_id
    assert hass.states.get(climate).state == "unavailable"
    controller.zones["zone_test"] = deepcopy(ZONE)
    await integration.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(climate).state == "heat"
    assert not await async_remove_config_entry_device(
        hass, integration, dr.async_get(hass).async_get(original.device_id)
    )
    assert not controller.patches


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, ConfigEntryState.SETUP_ERROR), (503, ConfigEntryState.SETUP_RETRY)],
)
async def test_initial_errors(hass, controller, entry, status, expected):
    controller.faults[("GET", "/api/v1/device")].append(web.Response(status=status))
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state == expected
    assert not controller.patches


@pytest.mark.parametrize("invalidated_token", ["rotated-token", ""])
async def test_token_rotation_or_revocation_recovers_without_replaying(
    hass, controller, integration, invalidated_token
):
    climate = entity_id(hass, "climate")
    unique_id = er.async_get(hass).async_get(climate).unique_id
    controller.token = invalidated_token
    await integration.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(climate).state == "unavailable"
    flow = next(
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["context"]["source"] == "reauth"
    )
    controller.token = "new-token"
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"origin": controller.origin, "token": controller.token}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert hass.states.get(climate).state == "heat"
    assert er.async_get(hass).async_get(climate).unique_id == unique_id
    assert integration.data["token"] == controller.token
    assert controller.authorization_headers[-1] == f"Bearer {controller.token}"
    assert all(method == "GET" for method, _, _ in controller.requests)


async def test_two_controllers_same_zone_id(hass, controller, integration, aiohttp_server):
    from .conftest import FakeController

    other = FakeController()
    other.device["id"] = "other_controller"
    app = web.Application()
    app.router.add_route("*", "/api/v1/{path:.*}", other.handle)
    server = await aiohttp_server(app)
    other.origin = str(server.make_url("/")).rstrip("/")
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=other.device["id"],
        data={"origin": other.origin, "token": other.token},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    climate_ids = [state.entity_id for state in hass.states.async_all("climate")]
    assert len(climate_ids) == 2
    assert len({registry.async_get(entity).unique_id for entity in climate_ids}) == 2
    await hass.config_entries.async_unload(entry.entry_id)
