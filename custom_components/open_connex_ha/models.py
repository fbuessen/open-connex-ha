"""Immutable API resources and validation, independent of Home Assistant."""

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

type Unit = Literal["celsius", "fahrenheit"]
type Mode = Literal["normal", "eco"]


class InvalidResponse(ValueError):
    """The controller did not return a supported resource."""


class InvalidValue(ValueError):
    """An input is outside the public API contract."""


@dataclass(frozen=True)
class Limits:
    """Inclusive bounds and step anchored at the minimum."""

    minimum: float
    maximum: float
    step: float

    def validate(self, value: object) -> float:
        """Reject invalid values without clamping or rounding."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InvalidValue("A finite number is required")
        try:
            valid = math.isfinite(value)
        except OverflowError:
            valid = False
        if not valid:
            raise InvalidValue("Value is outside the supported range")
        steps = (Decimal(str(value)) - Decimal(str(self.minimum))) / Decimal(str(self.step))
        # Allow conversion's floating-point residue, never a genuine off-grid value.
        if abs(steps - steps.to_integral_value()) > Decimal("1e-9"):
            raise InvalidValue("Value is not on a supported step")
        # Normalize only the conversion residue accepted above to an exact wire step.
        normalized = float(
            Decimal(str(self.minimum)) + steps.to_integral_value() * Decimal(str(self.step))
        )
        if not self.minimum <= normalized <= self.maximum:
            raise InvalidValue("Value is outside the supported range")
        return normalized


TARGET_LIMITS: dict[Unit, Limits] = {
    "celsius": Limits(10, 30, 0.5),
    "fahrenheit": Limits(50, 86, 0.5),
}
OFFSET_LIMITS = Limits(-9, 9, 1)


def _string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise InvalidResponse("Missing or invalid string field")
    return value


@dataclass(frozen=True)
class Device:
    """Controller identity and readiness, never heater health."""

    id: str
    firmware_version: str
    health: Literal["ok", "degraded"]

    @classmethod
    def parse(cls, data: Any) -> Device:
        if not isinstance(data, dict) or data.get("health") not in ("ok", "degraded"):
            raise InvalidResponse("Invalid device resource")
        return cls(_string(data, "id"), _string(data, "firmware_version"), data["health"])


@dataclass(frozen=True)
class Zone:
    """Latest accepted zone settings, not measured heater state."""

    id: str
    name: str
    target_temperature: float
    mode: Mode
    eco_offset: float
    unit: Unit

    @property
    def active_offset(self) -> float:
        """Offset applied to the base target by the current preset."""
        return self.eco_offset if self.mode == "eco" else 0

    @property
    def effective_temperature(self) -> float:
        return self.target_temperature + self.active_offset

    @property
    def effective_limits(self) -> Limits:
        limits = TARGET_LIMITS[self.unit]
        return Limits(
            limits.minimum + self.active_offset,
            limits.maximum + self.active_offset,
            limits.step,
        )

    @classmethod
    def parse(cls, data: Any) -> Zone:
        if not isinstance(data, dict) or not isinstance(data.get("configuration"), dict):
            raise InvalidResponse("Invalid zone resource")
        config = data["configuration"]
        if config.get("unit") not in ("celsius", "fahrenheit") or config.get("mode") not in (
            "normal",
            "eco",
        ):
            raise InvalidResponse("Unsupported unit or mode")
        if "target_temperature" not in config:
            raise InvalidResponse("Missing target temperature")
        try:
            target = TARGET_LIMITS[config["unit"]].validate(config["target_temperature"])
            offset = OFFSET_LIMITS.validate(config.get("eco_offset"))
        except InvalidValue as err:
            raise InvalidResponse("Unsupported numeric configuration") from err
        return cls(
            _string(data, "id"),
            _string(data, "name"),
            target,
            config["mode"],
            offset,
            config["unit"],
        )


def parse_zones(data: Any) -> dict[str, Zone]:
    """Reject a malformed inventory atomically, including duplicate identities."""
    if not isinstance(data, dict) or not isinstance(data.get("zones"), list):
        raise InvalidResponse("Invalid zone inventory")
    zones: dict[str, Zone] = {}
    for item in data["zones"]:
        zone = Zone.parse(item)
        if zone.id in zones:
            raise InvalidResponse("Duplicate zone identity")
        zones[zone.id] = zone
    return zones
