"""Open Connex HA integration for the Connex REST API."""

import ssl
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ConnexClient
from .const import CONF_CA_CERTIFICATE, CONF_ORIGIN, DOMAIN, PLATFORMS
from .coordinator import ConnexCoordinator


@dataclass
class RuntimeData:
    coordinator: ConnexCoordinator


type ConnexConfigEntry = ConfigEntry[RuntimeData]


async def async_create_client(hass: HomeAssistant, data: dict) -> ConnexClient:
    """Build verified TLS context off the event loop, when a CA was supplied."""
    context = None
    if certificate := data.get(CONF_CA_CERTIFICATE):
        context = await hass.async_add_executor_job(
            lambda: ssl.create_default_context(cadata=certificate)
        )
    return ConnexClient(async_get_clientsession(hass), data[CONF_ORIGIN], data[CONF_TOKEN], context)


async def async_setup_entry(hass: HomeAssistant, entry: ConnexConfigEntry) -> bool:
    client = await async_create_client(hass, dict(entry.data))
    coordinator = ConnexCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = RuntimeData(coordinator)
    device = coordinator.data.device
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, device.id)},
        name="Connex controller",
        manufacturer="Dimplex",
        model="Connex controller",
        sw_version=device.firmware_version,
        configuration_url=client.origin,
    )
    # Keeps inventory discovery active even with zero zones or all entities disabled.
    entry.async_on_unload(coordinator.async_add_listener(lambda: None))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConnexConfigEntry) -> bool:
    await entry.runtime_data.coordinator.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConnexConfigEntry) -> None:
    ir.async_delete_issue(hass, DOMAIN, f"identity_{entry.entry_id}")


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: ConnexConfigEntry,
    device: dr.DeviceEntry,
) -> bool:
    """Allow local cleanup only for zones absent from a successful inventory."""
    from .entity import zone_identifier

    if not hasattr(entry, "runtime_data"):
        return False
    coordinator = entry.runtime_data.coordinator
    if not coordinator.last_update_success:
        return False
    active = {(DOMAIN, coordinator.data.device.id)}
    active.update(
        (DOMAIN, zone_identifier(coordinator.data.device.id, zone_id))
        for zone_id in coordinator.data.zones
    )
    return not bool(device.identifiers & active)
