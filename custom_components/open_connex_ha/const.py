"""Integration constants."""

from homeassistant.const import Platform

DOMAIN = "open_connex_ha"
VERSION = "0.2.0"
PLATFORMS = (Platform.CLIMATE, Platform.NUMBER, Platform.SENSOR)
CONF_ORIGIN = "origin"
CONF_CA_CERTIFICATE = "ca_certificate"
POLL_SECONDS = 30
MAX_BACKOFF_SECONDS = 300
