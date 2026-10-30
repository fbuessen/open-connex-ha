"""Public numeric constraints and strict resource validation."""

from copy import deepcopy

import pytest

from custom_components.open_connex_ha.models import (
    OFFSET_LIMITS,
    TARGET_LIMITS,
    Device,
    InvalidResponse,
    InvalidValue,
    Zone,
    parse_zones,
)

from .conftest import DEVICE, ZONE


@pytest.mark.parametrize(
    ("unit", "values"),
    [("celsius", [10, 10.5, 21, 29.5, 30]), ("fahrenheit", [50, 50.5, 68, 85.5, 86])],
)
def test_target_limits(unit, values):
    for value in values:
        assert TARGET_LIMITS[unit].validate(value) == value


@pytest.mark.parametrize(
    ("value", "expected"), [(10 - 1e-14, 10), (30 + 1e-14, 30), (21 + 1e-14, 21)]
)
def test_normalize_conversion_residue(value, expected):
    assert TARGET_LIMITS["celsius"].validate(value) == expected


@pytest.mark.parametrize(
    "value",
    [9.5, 30.5, 9.999, 30.001, 21.1, None, True, "21", float("nan"), float("inf"), 10**1000],
)
def test_rejected_targets(value):
    with pytest.raises(InvalidValue):
        TARGET_LIMITS["celsius"].validate(value)


@pytest.mark.parametrize("value", [-9, -1, 0, 1, 9])
def test_offsets(value):
    assert OFFSET_LIMITS.validate(value) == value


@pytest.mark.parametrize("value", [-10, 10, 0.5, True, None])
def test_rejected_offsets(value):
    with pytest.raises(InvalidValue):
        OFFSET_LIMITS.validate(value)


def test_extra_fields():
    zone = deepcopy(ZONE)
    zone["future"] = "ignored"
    assert Zone.parse(zone).target_temperature == ZONE["configuration"]["target_temperature"]
    assert Device.parse({**DEVICE, "future": True}).id == DEVICE["id"]


@pytest.mark.parametrize(
    "bad", [{}, None, {"zones": None}, {"zones": [ZONE, ZONE]}, {"zones": [{}]}]
)
def test_bad_inventory(bad):
    with pytest.raises(InvalidResponse):
        parse_zones(bad)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mode", "off"),
        ("unit", "kelvin"),
        ("unit", []),
        ("target_temperature", False),
        ("target_temperature", None),
        ("eco_offset", 1.5),
    ],
)
def test_bad_configuration(field, value):
    zone = deepcopy(ZONE)
    zone["configuration"][field] = value
    with pytest.raises(InvalidResponse):
        Zone.parse(zone)
