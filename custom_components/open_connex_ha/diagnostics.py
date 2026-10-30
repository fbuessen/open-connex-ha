"""An allowlist of diagnostics which never includes credentials or identities."""

from typing import Any

from homeassistant.core import HomeAssistant

from . import ConnexConfigEntry
from .const import VERSION


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConnexConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data.coordinator
    return {
        "integration_version": VERSION,
        "firmware_version": coordinator.data.device.firmware_version,
        "health": coordinator.data.device.health,
        "zone_count": len(coordinator.data.zones),
        "stale_zone_count": len(coordinator.data.stale),
        "last_update_success": coordinator.last_update_success,
        "last_error": coordinator.last_error,
        "authentication_required": coordinator.auth_failed,
        "identity_mismatch": coordinator.identity_mismatch,
    }
