"""Acceptance, uncertain outcomes, concurrency, and shutdown regressions."""

import asyncio
from copy import deepcopy

import pytest
from aiohttp import web
from homeassistant.exceptions import HomeAssistantError

from .test_integration import entity_id


async def change_target(hass, value=22):
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": entity_id(hass, "climate"), "temperature": value},
        blocking=True,
    )


@pytest.mark.parametrize("status", [401, 409, 429, 503])
async def test_mutation_not_retried(hass, controller, integration, status):
    controller.faults[("PATCH", "/api/v1/zones/zone_test")].append(web.Response(status=status))
    with pytest.raises(HomeAssistantError):
        await change_target(hass)
    await hass.async_block_till_done()
    assert sum(method == "PATCH" for method, _, _ in controller.requests) == 1
    assert not controller.patches


async def test_standby_between_preflight_and_write_is_a_rejection(hass, controller, integration):
    async def enter_standby(request):
        controller.device["health"] = "degraded"
        return web.Response(status=503)

    controller.faults[("PATCH", "/api/v1/zones/zone_test")].append(enter_standby)
    with pytest.raises(HomeAssistantError) as raised:
        await change_target(hass)
    assert raised.value.translation_key == "unavailable"
    assert controller.zones["zone_test"]["configuration"]["target_temperature"] == 21
    assert not controller.patches
    assert hass.states.get(entity_id(hass, "climate")).state == "unavailable"
    # Unlike a lost response, the explicit 503 does not require reconciliation.
    assert controller.requests[-1][0] == "PATCH"
    await integration.runtime_data.coordinator.async_refresh()
    assert hass.states.get(entity_id(hass, "sensor")).state == "degraded"
    controller.device["health"] = "ok"
    await integration.runtime_data.coordinator.async_refresh()
    assert hass.states.get(entity_id(hass, "climate")).state == "heat"
    assert sum(method == "PATCH" for method, _, _ in controller.requests) == 1


async def test_standby_preflight_blocks_writes(hass, controller, integration):
    controller.device["health"] = "degraded"
    with pytest.raises(HomeAssistantError) as raised:
        await change_target(hass)
    assert raised.value.translation_key == "unavailable"
    assert not any(method == "PATCH" for method, _, _ in controller.requests)
    assert hass.states.get(entity_id(hass, "sensor")).state == "degraded"
    controller.device["health"] = "ok"
    await integration.runtime_data.coordinator.async_refresh()
    assert hass.states.get(entity_id(hass, "climate")).state == "heat"
    assert not controller.patches


@pytest.mark.parametrize("status", [503, 401])
async def test_accepted_then_failed_read(hass, controller, integration, status):
    async def accept(request):
        controller.zones["zone_test"]["configuration"]["target_temperature"] = 22
        controller.faults[("GET", "/api/v1/zones/zone_test")].append(web.Response(status=status))
        return web.Response(status=202)

    controller.faults[("PATCH", "/api/v1/zones/zone_test")].append(accept)
    await change_target(hass)  # Accepted: no action exception despite failed read.
    await hass.async_block_till_done()
    coordinator = integration.runtime_data.coordinator
    assert hass.states.get(entity_id(hass, "climate")).state == "unavailable"
    assert coordinator.auth_failed is (status == 401)
    if status == 401:
        assert any(
            flow["context"]["source"] == "reauth"
            for flow in hass.config_entries.flow.async_progress()
        )
    else:
        await coordinator.async_refresh()
        assert hass.states.get(entity_id(hass, "climate")).attributes["temperature"] == 22
    assert sum(method == "PATCH" for method, _, _ in controller.requests) == 1


@pytest.mark.parametrize("failed_read", [False, True])
async def test_lost_write_response(hass, controller, integration, failed_read):
    async def lose_response(request):
        controller.zones["zone_test"]["configuration"]["target_temperature"] = 22
        if failed_read:
            controller.faults[("GET", "/api/v1/zones/zone_test")].append(web.Response(status=503))
        request.transport.close()
        return web.Response(status=202)

    controller.faults[("PATCH", "/api/v1/zones/zone_test")].append(lose_response)
    with pytest.raises(HomeAssistantError) as raised:
        await change_target(hass)
    assert raised.value.translation_key == "unknown_outcome"
    climate = entity_id(hass, "climate")
    if failed_read:
        assert hass.states.get(climate).state == "unavailable"
        await integration.runtime_data.coordinator.async_refresh()
    assert hass.states.get(climate).attributes["temperature"] == 22
    assert sum(method == "PATCH" for method, _, _ in controller.requests) == 1


async def test_stale_unit_rejected_and_external_fields_preserved(hass, controller, integration):
    controller.zones["zone_test"]["configuration"].update(
        unit="fahrenheit", target_temperature=68, eco_offset=2
    )
    with pytest.raises(HomeAssistantError) as raised:
        await change_target(hass)
    assert raised.value.translation_key == "unit_changed"
    assert not controller.patches
    # 20 C in HA converts to the now-known native 68 F.
    await change_target(hass, 20)
    assert controller.patches == [{"target_temperature": 68}]
    assert controller.zones["zone_test"]["configuration"]["eco_offset"] == 2


@pytest.mark.parametrize(("mode", "offset", "base"), [("eco", -3, 25), ("normal", -3, 22)])
async def test_effective_target_uses_fresh_preset_and_offset(
    hass, controller, integration, mode, offset, base
):
    controller.zones["zone_test"]["configuration"].update(mode=mode, eco_offset=offset)
    await change_target(hass, 22)
    assert controller.patches == [{"target_temperature": base}]
    assert hass.states.get(entity_id(hass, "climate")).attributes["temperature"] == 22


async def test_effective_target_rejects_new_offset_out_of_range(hass, controller, integration):
    controller.zones["zone_test"]["configuration"].update(mode="eco", eco_offset=-9)
    with pytest.raises(HomeAssistantError) as raised:
        await change_target(hass, 22)  # Would require a 31°C base target.
    assert raised.value.translation_key == "invalid_value"
    assert not controller.patches


async def test_target_conversion_residue_is_normalized(hass, controller, integration):
    await change_target(hass, 21.000000000000004)
    assert controller.patches == [{"target_temperature": 21.0}]


async def test_poll_and_commands_are_ordered(hass, controller, integration):
    entered = asyncio.Event()
    release = asyncio.Event()
    snapshot = deepcopy(list(controller.zones.values()))

    async def slow_poll(request):
        entered.set()
        await release.wait()
        return web.json_response({"zones": snapshot})

    controller.faults[("GET", "/api/v1/zones")].append(slow_poll)
    poll = asyncio.create_task(integration.runtime_data.coordinator.async_refresh())
    await asyncio.wait_for(entered.wait(), 2)
    first = asyncio.create_task(change_target(hass, 22))
    second = asyncio.create_task(
        hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": entity_id(hass, "number"), "value": 3},
            blocking=True,
        )
    )
    release.set()
    await asyncio.gather(poll, first, second)
    assert controller.patches == [{"target_temperature": 22}, {"eco_offset": 3}]
    assert hass.states.get(entity_id(hass, "climate")).attributes["temperature"] == 22


@pytest.mark.parametrize("waiting_on_lock", [False, True])
async def test_unload_prevents_unsent_commands(hass, controller, integration, waiting_on_lock):
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_device(request):
        entered.set()
        await release.wait()
        return web.json_response(controller.device)

    controller.faults[("GET", "/api/v1/device")].append(slow_device)
    first = asyncio.create_task(change_target(hass))
    await asyncio.wait_for(entered.wait(), 2)
    tasks = [first]
    if waiting_on_lock:
        tasks.append(asyncio.create_task(change_target(hass, 23)))
        await asyncio.sleep(0)
    await hass.config_entries.async_unload(integration.entry_id)
    release.set()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert not controller.patches
    assert not any(method == "PATCH" for method, _, _ in controller.requests)
