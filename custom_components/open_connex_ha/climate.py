"""Effective requested zone temperature and Normal/Eco controls."""

from typing import Any

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ConnexConfigEntry
from .coordinator import ConnexCoordinator, action_error
from .entity import ConnexZoneEntity, async_discover_zones
from .models import TARGET_LIMITS

PARALLEL_UPDATES = 0  # The coordinator serializes across every platform.
# HA checks bounds before our validator can normalize conversion residue (e.g.
# 33.8°F becomes 0.9999999999999984°C). Displayed bounds still round exactly.
_BOUNDARY_TOLERANCE = 1e-9


async def async_setup_entry(
    hass: HomeAssistant, entry: ConnexConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    entry.async_on_unload(
        async_discover_zones(
            coordinator, async_add_entities, lambda zone_id: ConnexClimate(coordinator, zone_id)
        )
    )


class ConnexClimate(ConnexZoneEntity, ClimateEntity):
    _attr_name = None
    _attr_translation_key = "zone"
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
    )
    _attr_hvac_modes = [HVACMode.HEAT]
    _attr_hvac_mode = HVACMode.HEAT
    _attr_preset_modes = ["normal", "eco"]
    _attr_current_temperature = None
    _attr_hvac_action = None
    _attr_precision = 0.1  # Preserve half-degree targets in Fahrenheit displays too.
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, coordinator: ConnexCoordinator, zone_id: str) -> None:
        super().__init__(coordinator, zone_id, "climate")

    @property
    def temperature_unit(self) -> UnitOfTemperature:
        return (
            UnitOfTemperature.FAHRENHEIT
            if self.zone and self.zone.unit == "fahrenheit"
            else UnitOfTemperature.CELSIUS
        )

    @property
    def target_temperature(self) -> float | None:
        return self.zone.effective_temperature if self.zone else None

    @property
    def preset_mode(self) -> str | None:
        return self.zone.mode if self.zone else None

    @property
    def min_temp(self) -> float:
        limits = self.zone.effective_limits if self.zone else TARGET_LIMITS["celsius"]
        return limits.minimum - _BOUNDARY_TOLERANCE

    @property
    def max_temp(self) -> float:
        limits = self.zone.effective_limits if self.zone else TARGET_LIMITS["celsius"]
        return limits.maximum + _BOUNDARY_TOLERANCE

    @property
    def target_temperature_step(self) -> float:
        return 0.5

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if kwargs.get("hvac_mode", HVACMode.HEAT) != HVACMode.HEAT:
            raise action_error("unsupported_mode", validation=True)
        if ATTR_TEMPERATURE not in kwargs or self.zone is None:
            raise action_error("invalid_value", validation=True)
        await self.coordinator.async_command(
            self.zone_id,
            {"target_temperature": kwargs[ATTR_TEMPERATURE]},
            unit=self.zone.unit,
        )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode not in self._attr_preset_modes:
            raise action_error("unsupported_mode", validation=True)
        await self.coordinator.async_command(self.zone_id, {"mode": preset_mode})

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode != HVACMode.HEAT:
            raise action_error("unsupported_mode", validation=True)

    async def async_turn_on(self) -> None:
        raise action_error("unsupported_mode", validation=True)

    async def async_turn_off(self) -> None:
        raise action_error("unsupported_mode", validation=True)

    async def async_toggle(self) -> None:
        raise action_error("unsupported_mode", validation=True)
