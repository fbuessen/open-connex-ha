"""Ordered polling and explicitly requested control transactions."""

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import timedelta
from types import MappingProxyType
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ApiError, ConnexClient
from .const import DOMAIN, MAX_BACKOFF_SECONDS, POLL_SECONDS
from .models import OFFSET_LIMITS, TARGET_LIMITS, Device, InvalidValue, Unit, Zone

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Snapshot:
    """A complete inventory plus explicit per-zone read failures."""

    device: Device
    zones: Mapping[str, Zone]
    stale: frozenset[str] = frozenset()
    generation: int = field(default=0, compare=False)


def action_error(key: str, *, validation: bool = False) -> HomeAssistantError:
    error_type = ServiceValidationError if validation else HomeAssistantError
    return error_type(translation_domain=DOMAIN, translation_key=key)


class ConnexCoordinator(DataUpdateCoordinator[Snapshot]):
    """One lock serializes all reads, commands and synchronous cache commits."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: ConnexClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=POLL_SECONDS),
            always_update=False,
        )
        self.client = client
        self.entry = entry
        self.closing = False
        self.auth_failed = False
        self.identity_mismatch = False
        self.last_error: str | None = None
        self._lock = asyncio.Lock()
        self._snapshot: Snapshot | None = None
        self._failures = 0
        self._tasks: set[asyncio.Task[Any]] = set()

    def _ensure_active(self) -> None:
        if self.closing:
            raise action_error("closing")
        if self.auth_failed:
            raise action_error("invalid_auth")

    def _check_device(self, device: Device) -> None:
        self.identity_mismatch = device.id != self.entry.unique_id
        issue_id = f"identity_{self.entry.entry_id}"
        if self.identity_mismatch:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key="identity_mismatch",
            )
            raise ApiError("identity_mismatch")
        ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    def _record_error(self, err: ApiError) -> None:
        self.last_error = err.code
        if err.status == 401 and not self.auth_failed:
            self.auth_failed = True
            self.entry.async_start_reauth(self.hass)

    def _commit(self, snapshot: Snapshot, *, notify: bool = True) -> Snapshot:
        """No await between committing and publishing a command's snapshot."""
        generation = self._snapshot.generation + 1 if self._snapshot else 1
        self._snapshot = replace(snapshot, generation=generation)
        if notify:
            self.async_set_updated_data(self._snapshot)
        return self._snapshot

    def _zone_snapshot(self, zone: Zone | None, zone_id: str, *, stale: bool = False) -> None:
        assert self._snapshot is not None
        zones = dict(self._snapshot.zones)
        if zone is not None:
            zones[zone.id] = zone
        unavailable = set(self._snapshot.stale)
        if stale:
            unavailable.add(zone_id)
        else:
            unavailable.discard(zone_id)
        self._commit(
            replace(self._snapshot, zones=MappingProxyType(zones), stale=frozenset(unavailable))
        )

    async def _async_update_data(self) -> Snapshot:
        task = asyncio.current_task()
        assert task is not None
        self._tasks.add(task)
        try:
            async with self._lock:
                if self.auth_failed:
                    raise ConfigEntryAuthFailed("Controller authentication required")
                self._ensure_active()
                try:
                    device = await self.client.get_device()
                    self._check_device(device)
                    zones = await self.client.get_zones()
                except ApiError as err:
                    self._record_error(err)
                    self._failures = min(self._failures + 1, 5)
                    delay = min(MAX_BACKOFF_SECONDS, POLL_SECONDS * 2 ** (self._failures - 1))
                    delay = max(delay, err.retry_after or 0)
                    self.update_interval = timedelta(seconds=delay)
                    if err.status == 401:
                        raise ConfigEntryAuthFailed("Controller authentication required") from None
                    raise UpdateFailed(err.code) from None
                self._ensure_active()
                self._failures = 0
                self.last_error = None
                self.update_interval = timedelta(seconds=POLL_SECONDS)
                # The framework publishes immediately after return, without an await.
                return self._commit(Snapshot(device, MappingProxyType(zones)), notify=False)
        finally:
            self._tasks.discard(task)

    async def async_command(
        self,
        zone_id: str,
        values: dict[str, Any],
        *,
        unit: Unit | None = None,
    ) -> None:
        """One explicit PATCH, followed only by reads, including on uncertainty."""
        task = asyncio.current_task()
        assert task is not None
        self._tasks.add(task)
        try:
            async with self._lock:
                self._ensure_active()
                if self._snapshot is None or zone_id not in self._snapshot.zones:
                    raise action_error("not_found", validation=True)
                if not self.last_update_success:
                    raise action_error("unavailable")
                try:
                    device = await self.client.get_device()
                    self._check_device(device)
                    self._commit(replace(self._snapshot, device=device))
                    if device.health != "ok":
                        raise action_error("unavailable")
                    zone = await self.client.get_zone(zone_id)
                    self._zone_snapshot(zone, zone_id)
                except ApiError as err:
                    self._record_error(err)
                    self._zone_snapshot(None, zone_id, stale=True)
                    raise action_error(err.code) from None

                if unit is not None and unit != zone.unit:
                    raise action_error("unit_changed", validation=True)
                if not values or set(values) - {"target_temperature", "mode", "eco_offset"}:
                    raise action_error("invalid_value", validation=True)
                try:
                    values = dict(values)
                    if "target_temperature" in values:
                        target = values["target_temperature"]
                        # Convert using the preflight's current mode and offset,
                        # inside the command lock, then send only the base target.
                        target = zone.effective_limits.validate(target) - zone.active_offset
                        values["target_temperature"] = TARGET_LIMITS[zone.unit].validate(target)
                    if "eco_offset" in values:
                        OFFSET_LIMITS.validate(values["eco_offset"])
                    if "mode" in values and values["mode"] not in ("normal", "eco"):
                        raise InvalidValue("Unsupported mode")
                except InvalidValue:
                    raise action_error("invalid_value", validation=True) from None

                self._ensure_active()
                try:
                    await self.client.patch_zone(zone_id, values)
                except ApiError as err:
                    self._record_error(err)
                    if err.uncertain:
                        await self._reconcile(zone_id)
                        raise action_error("unknown_outcome") from None
                    if err.status in (401, 404, 503):
                        self._zone_snapshot(None, zone_id, stale=True)
                    raise action_error(err.code, validation=err.status in (400, 422)) from None
                # The command is accepted even if this read fails or needs reauth.
                if not await self._reconcile(zone_id):
                    _LOGGER.warning(
                        "Controller accepted a command, but saved settings could not be refreshed"
                    )
        finally:
            self._tasks.discard(task)

    async def _reconcile(self, zone_id: str) -> bool:
        try:
            zone = await self.client.get_zone(zone_id)
        except ApiError as err:
            self._record_error(err)
            self._zone_snapshot(None, zone_id, stale=True)
            return False
        self._zone_snapshot(zone, zone_id)
        return True

    async def async_stop(self) -> None:
        """Stop new work before cancelling any requests already in flight."""
        self.closing = True
        tasks = self._tasks - {asyncio.current_task()}
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.async_shutdown()
