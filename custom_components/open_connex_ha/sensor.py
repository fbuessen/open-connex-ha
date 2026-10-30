"""Controller readiness, with no implied heater feedback."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import ConnexConfigEntry
from .const import DOMAIN
from .coordinator import ConnexCoordinator
from .entity import entity_identifier

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConnexConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([ConnexHealth(entry.runtime_data.coordinator)])


class ConnexHealth(CoordinatorEntity[ConnexCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "health"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["ok", "degraded"]

    def __init__(self, coordinator: ConnexCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = entity_identifier(coordinator.data.device.id, None, "health")

    @property
    def available(self) -> bool:
        return (
            super().available
            and not self.coordinator.closing
            and not self.coordinator.auth_failed
            and not self.coordinator.identity_mismatch
        )

    @property
    def native_value(self) -> str:
        return self.coordinator.data.device.health

    @property
    def device_info(self) -> DeviceInfo:
        device = self.coordinator.data.device
        return DeviceInfo(
            identifiers={(DOMAIN, device.id)},
            name="Connex controller",
            manufacturer="Dimplex",
            model="Connex controller",
            sw_version=device.firmware_version,
            configuration_url=self.coordinator.client.origin,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        if self.device_entry:
            dr.async_get(self.hass).async_update_device(
                self.device_entry.id, sw_version=self.coordinator.data.device.firmware_version
            )
        super()._handle_coordinator_update()
