"""Stable identities and shared availability for logical zones."""

import json
from collections.abc import Callable

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ConnexCoordinator
from .models import Zone


def zone_identifier(controller_id: str, zone_id: str) -> str:
    return json.dumps([controller_id, zone_id], separators=(",", ":"), ensure_ascii=True)


def entity_identifier(controller_id: str, zone_id: str | None, kind: str) -> str:
    return json.dumps([controller_id, zone_id, kind], separators=(",", ":"), ensure_ascii=True)


@callback
def async_discover_zones(
    coordinator: ConnexCoordinator, add: AddEntitiesCallback, factory: Callable[[str], Entity]
) -> Callable[[], None]:
    """Register one inventory listener per platform and discover existing zones."""
    known: set[str] = set()

    @callback
    def discover() -> None:
        new = set(coordinator.data.zones) - known
        if new:
            known.update(new)
            add(factory(zone_id) for zone_id in sorted(new))

    remove = coordinator.async_add_listener(discover)
    discover()
    return remove


class ConnexZoneEntity(CoordinatorEntity[ConnexCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: ConnexCoordinator, zone_id: str, kind: str) -> None:
        super().__init__(coordinator)
        self.zone_id = zone_id
        self._attr_unique_id = entity_identifier(coordinator.data.device.id, zone_id, kind)

    @property
    def zone(self) -> Zone | None:
        return self.coordinator.data.zones.get(self.zone_id)

    @property
    def available(self) -> bool:
        return (
            super().available
            and not self.coordinator.closing
            and not self.coordinator.auth_failed
            and not self.coordinator.identity_mismatch
            and self.zone is not None
            and self.zone_id not in self.coordinator.data.stale
            and self.coordinator.data.device.health == "ok"
        )

    @property
    def device_info(self) -> DeviceInfo:
        device = self.coordinator.data.device
        controller = dr.async_get(self.coordinator.hass).async_get_device_by_identifier(
            (DOMAIN, device.id),
            self.coordinator.entry.entry_id,
        )
        assert controller is not None
        return DeviceInfo(
            identifiers={(DOMAIN, zone_identifier(device.id, self.zone_id))},
            name=self.zone.name if self.zone else None,
            manufacturer="Dimplex",
            model="Connex zone",
            via_device_id=controller.id,
            configuration_url=self.coordinator.client.origin,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        if self.zone and self.device_entry:
            dr.async_get(self.hass).async_update_device(self.device_entry.id, name=self.zone.name)
        super()._handle_coordinator_update()
