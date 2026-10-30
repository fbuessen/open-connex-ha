"""Conversion through Home Assistant's climate and number action handlers."""

import pytest
from homeassistant.const import UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_conversion import TemperatureConverter
from homeassistant.util.unit_system import IMPERIAL_SYSTEM, METRIC_SYSTEM

from .test_integration import entity_id


@pytest.mark.parametrize("unit", ["celsius", "fahrenheit"])
@pytest.mark.parametrize("system", [METRIC_SYSTEM, IMPERIAL_SYSTEM])
async def test_climate_conversion_and_boundaries(hass, controller, entry, unit, system):
    hass.config.units = system
    controller.zones["zone_test"]["configuration"].update(
        unit=unit, target_temperature=20 if unit == "celsius" else 68
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    climate = entity_id(hass, "climate")
    display_celsius = system is METRIC_SYSTEM
    assert hass.states.get(climate).attributes["temperature"] == (20 if display_celsius else 68)
    for display_value, native_value in [
        (10 if display_celsius else 50, 10 if unit == "celsius" else 50),
        (30 if display_celsius else 86, 30 if unit == "celsius" else 86),
    ]:
        await hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": climate, "temperature": display_value},
            blocking=True,
        )
        assert controller.patches[-1] == {"target_temperature": native_value}
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("unit", ["celsius", "fahrenheit"])
@pytest.mark.parametrize("system", [METRIC_SYSTEM, IMPERIAL_SYSTEM])
async def test_delta_number_native_default(hass, controller, entry, unit, system):
    hass.config.units = system
    controller.zones["zone_test"]["configuration"].update(
        unit=unit, target_temperature=20 if unit == "celsius" else 68
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    number = entity_id(hass, "number")
    assert hass.states.get(number).attributes["unit_of_measurement"] == (
        "°C" if unit == "celsius" else "°F"
    )
    await hass.services.async_call(
        "number", "set_value", {"entity_id": number, "value": 1}, blocking=True
    )
    assert controller.patches == [{"eco_offset": 1}]
    await hass.config_entries.async_unload(entry.entry_id)


async def test_number_unit_preference_is_an_interval(hass, controller, integration):
    number = entity_id(hass, "number")
    er.async_get(hass).async_update_entity_options(number, "number", {"unit_of_measurement": "°F"})
    await hass.async_block_till_done()
    assert not controller.patches
    await hass.services.async_call(
        "number", "set_value", {"entity_id": number, "value": 1.8}, blocking=True
    )
    assert controller.patches == [{"eco_offset": 1}]
    assert float(hass.states.get(number).state) == pytest.approx(1.8)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "number", "set_value", {"entity_id": number, "value": 1}, blocking=True
        )
    assert len(controller.patches) == 1


async def test_dynamic_unit_properties(hass, controller, integration):
    controller.zones["zone_test"]["configuration"].update(
        unit="fahrenheit", target_temperature=86, eco_offset=9
    )
    await integration.runtime_data.coordinator.async_refresh()
    climate = hass.states.get(entity_id(hass, "climate"))
    number = hass.states.get(entity_id(hass, "number"))
    assert climate.attributes["temperature"] == 30
    assert climate.attributes["min_temp"] == 10
    assert climate.attributes["max_temp"] == 30
    assert number.attributes["unit_of_measurement"] == "°F"
    assert float(number.state) == 9
    assert not controller.patches


async def test_fahrenheit_half_degree_is_not_rounded_to_whole(hass, controller, entry):
    hass.config.units = IMPERIAL_SYSTEM
    controller.zones["zone_test"]["configuration"].update(
        unit="fahrenheit", target_temperature=68.5
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id(hass, "climate")).attributes["temperature"] == 68.5
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("offset", [-9, 0, 9])
@pytest.mark.parametrize("unit", ["celsius", "fahrenheit"])
@pytest.mark.parametrize("system", [METRIC_SYSTEM, IMPERIAL_SYSTEM])
async def test_eco_effective_temperature_and_bounds(hass, controller, entry, unit, system, offset):
    hass.config.units = system
    native_unit = UnitOfTemperature.CELSIUS if unit == "celsius" else UnitOfTemperature.FAHRENHEIT

    def display(value):
        return TemperatureConverter.convert(value, native_unit, system.temperature_unit)

    base = 20 if unit == "celsius" else 68
    low, high = (10, 30) if unit == "celsius" else (50, 86)
    controller.zones["zone_test"]["configuration"].update(
        unit=unit, target_temperature=base, mode="eco", eco_offset=offset
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    climate = entity_id(hass, "climate")
    state = hass.states.get(climate)
    assert state.attributes["temperature"] == pytest.approx(display(base + offset), abs=0.05)
    assert state.attributes["min_temp"] == pytest.approx(display(low + offset), abs=0.05)
    assert state.attributes["max_temp"] == pytest.approx(display(high + offset), abs=0.05)
    for native_value in (low, high):
        await hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": climate, "temperature": display(native_value + offset)},
            blocking=True,
        )
        assert controller.patches[-1] == {"target_temperature": native_value}
    for native_value in (low - 0.5, high + 0.5, base + 0.1):
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                "climate",
                "set_temperature",
                {"entity_id": climate, "temperature": display(native_value + offset)},
                blocking=True,
            )
    assert len(controller.patches) == 2
    await hass.services.async_call(
        "climate",
        "set_preset_mode",
        {"entity_id": climate, "preset_mode": "normal"},
        blocking=True,
    )
    assert controller.patches[-1] == {"mode": "normal"}
    assert hass.states.get(climate).attributes["temperature"] == display(high)
    assert hass.states.get(climate).attributes["min_temp"] == display(low)
    assert hass.states.get(climate).attributes["max_temp"] == display(high)
    await hass.config_entries.async_unload(entry.entry_id)
