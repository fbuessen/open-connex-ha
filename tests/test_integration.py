"""Exercise real Home Assistant services, entity state and registries."""

import json
from copy import deepcopy
from datetime import timedelta

import pytest
from aiohttp import web
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.open_connex_ha.diagnostics import async_get_config_entry_diagnostics

from .conftest import ZONE


def entity_id(hass, domain):
    return next(state.entity_id for state in hass.states.async_all(domain))


async def test_setup_and_actions(hass, controller, integration):
    climate = entity_id(hass, "climate")
    state = hass.states.get(climate)
    assert state.state == "heat"
    assert state.attributes["temperature"] == 21
    assert state.attributes["current_temperature"] is None
    assert state.attributes.get("hvac_action") is None
    assert state.attributes["hvac_modes"] == ["heat"]
    assert state.attributes["preset_modes"] == ["normal", "eco"]
    assert all(method == "GET" for method, _, _ in controller.requests)
    for service, data, expected in [
        ("set_temperature", {"temperature": 22.5}, {"target_temperature": 22.5}),
        ("set_preset_mode", {"preset_mode": "eco"}, {"mode": "eco"}),
        ("set_temperature", {"temperature": 22.5}, {"target_temperature": 22.5}),
    ]:
        await hass.services.async_call(
            "climate", service, {"entity_id": climate, **data}, blocking=True
        )
        assert controller.patches[-1] == expected
    assert len(controller.patches) == 3
    number = entity_id(hass, "number")
    await hass.services.async_call(
        "number", "set_value", {"entity_id": number, "value": -9}, blocking=True
    )
    assert controller.patches[-1] == {"eco_offset": -9}
    assert hass.states.get(number).state == "-9.0"
    assert hass.states.get(climate).attributes["temperature"] == 13.5


@pytest.mark.parametrize(
    ("service", "data"),
    [
        ("set_temperature", {"temperature": 22, "hvac_mode": "off"}),
        ("set_hvac_mode", {"hvac_mode": "off"}),
        ("turn_on", {}),
        ("turn_off", {}),
        ("toggle", {}),
        ("set_temperature", {"temperature": 21.1}),
        ("set_temperature", {"temperature": 31}),
    ],
)
async def test_unsupported_actions_do_not_write(hass, controller, integration, service, data):
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "climate", service, {"entity_id": entity_id(hass, "climate"), **data}, blocking=True
        )
    assert not controller.patches


async def test_null_target_rejected(hass, controller, entry):
    controller.zones["zone_test"]["configuration"]["target_temperature"] = None
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state == ConfigEntryState.SETUP_RETRY
    assert not hass.states.async_all("climate")
    assert all(method == "GET" for method, _, _ in controller.requests)
    assert not controller.patches


async def test_dynamic_inventory_and_rename(hass, controller, integration):
    coordinator = integration.runtime_data.coordinator
    climate = entity_id(hass, "climate")
    registry = er.async_get(hass)
    original = registry.async_get(climate)
    controller.zones["zone_test"]["name"] = "Renamed"
    device_registry = dr.async_get(hass)
    device_registry.async_update_device(original.device_id, name_by_user="My name")
    await coordinator.async_refresh()
    device = device_registry.async_get(original.device_id)
    assert device.name == "Renamed"
    assert device.name_by_user == "My name"
    controller.zones.clear()
    await coordinator.async_refresh()
    assert hass.states.get(climate).state == "unavailable"
    new = deepcopy(ZONE)
    new["id"] = "new_zone"
    controller.zones[new["id"]] = new
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert len(hass.states.async_all("climate")) == 2
    controller.zones["zone_test"] = deepcopy(ZONE)
    await coordinator.async_refresh()
    assert hass.states.get(climate).state == "heat"
    assert registry.async_get(climate).unique_id == original.unique_id


async def test_empty_inventory_keeps_polling(hass, controller, entry):
    controller.zones.clear()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert not hass.states.async_all("climate")
    controller.zones["zone_test"] = deepcopy(ZONE)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=40))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(hass.states.async_all("climate")) == 1
    await hass.config_entries.async_unload(entry.entry_id)


async def test_degraded_and_bad_inventory(hass, controller, integration):
    coordinator = integration.runtime_data.coordinator
    climate = entity_id(hass, "climate")
    controller.device["health"] = "degraded"
    await coordinator.async_refresh()
    assert hass.states.get(climate).state == "unavailable"
    assert hass.states.get(entity_id(hass, "sensor")).state == "degraded"
    controller.device["health"] = "ok"
    controller.faults[("GET", "/api/v1/zones")].append(web.json_response({"zones": [{}]}))
    await coordinator.async_refresh()
    assert "zone_test" in coordinator.data.zones
    assert hass.states.get(climate).state == "unavailable"
    await coordinator.async_refresh()
    assert hass.states.get(climate).state == "heat"


async def test_identity_change_blocks_control(hass, controller, integration):
    controller.device["id"] = "replacement"
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": entity_id(hass, "climate"), "temperature": 22},
            blocking=True,
        )
    assert not controller.patches
    assert hass.states.get(entity_id(hass, "climate")).state == "unavailable"


async def test_diagnostics_and_unload(hass, controller, integration):
    payload = json.dumps(await async_get_config_entry_diagnostics(hass, integration))
    for secret in (
        controller.token,
        controller.origin,
        "controller_test",
        "zone_test",
        "Living room",
    ):
        assert secret not in payload
    await hass.config_entries.async_unload(integration.entry_id)
    before = len(controller.requests)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=10))
    await hass.async_block_till_done()
    assert len(controller.requests) == before
    assert not controller.patches
