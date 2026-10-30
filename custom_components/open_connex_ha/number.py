"""Signed energy-saving offset, a temperature interval."""

from homeassistant.components.number import NumberDeviceClass, NumberEntity
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ConnexConfigEntry
from .coordinator import ConnexCoordinator, action_error
from .entity import ConnexZoneEntity, async_discover_zones
from .models import OFFSET_LIMITS

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConnexConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    entry.async_on_unload(
        async_discover_zones(
            coordinator, async_add_entities, lambda zone_id: ConnexOffset(coordinator, zone_id)
        )
    )


class ConnexOffset(ConnexZoneEntity, NumberEntity):
    _attr_translation_key = "eco_offset"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.TEMPERATURE_DELTA
    _attr_native_min_value = OFFSET_LIMITS.minimum
    _attr_native_max_value = OFFSET_LIMITS.maximum
    _attr_native_step = OFFSET_LIMITS.step

    def __init__(self, coordinator: ConnexCoordinator, zone_id: str) -> None:
        super().__init__(coordinator, zone_id, "eco_offset")

    @property
    def native_value(self) -> float | None:
        return self.zone.eco_offset if self.zone else None

    @property
    def native_unit_of_measurement(self) -> UnitOfTemperature:
        return (
            UnitOfTemperature.FAHRENHEIT
            if self.zone and self.zone.unit == "fahrenheit"
            else UnitOfTemperature.CELSIUS
        )

    async def async_set_native_value(self, value: float) -> None:
        if self.zone is None:
            raise action_error("not_found", validation=True)
        await self.coordinator.async_command(
            self.zone_id, {"eco_offset": value}, unit=self.zone.unit
        )
