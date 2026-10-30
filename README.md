# Open Connex HA

Control your Dimplex Connex heaters from Home Assistant through [Open Connex Controller](https://github.com/fbuessen/open-connex-controller). Set temperatures, switch between Normal and Eco, and use your zones in Home Assistant automations. Everything runs on your local network.

- **One thermostat per zone**, with target temperature and Normal/Eco presets.
- **Adjustable Eco offsets**, with Celsius and Fahrenheit support.
- **Automatic zone discovery** and a controller health sensor.

The integration shows settings saved by the controller. Connex heaters do not report room temperature, heating activity, or connectivity, and there is no heater off command.

## Before you start

You need **Home Assistant Core 2026.10.0 or later** and an Open Connex Controller reachable from Home Assistant. This integration is tested with Core 2026.10.0 and controller API version 1 as implemented by firmware 0.2.9.

Set up the controller's Wi-Fi, create zones, and pair your heaters using the [controller setup guide](https://github.com/fbuessen/open-connex-controller#get-started). Check that temperature changes work from its web interface before adding Home Assistant.

## Installation

### With HACS

1. Open **HACS → ⋮ → Custom repositories**.
2. Add `https://github.com/fbuessen/open-connex-ha` with the category **Integration**.
3. Find **Open Connex HA** in HACS and download it.
4. Restart Home Assistant.

See [HACS custom repositories](https://www.hacs.xyz/docs/faq/custom_repositories/) if you need help adding the repository.

### Manual installation

1. Copy `custom_components/open_connex_ha/` into your Home Assistant configuration directory. The manifest should be at `/config/custom_components/open_connex_ha/manifest.json`.
2. Restart Home Assistant.

### Connect your controller

1. Sign in to the controller's web interface and generate or copy an API token under **Settings → Security**.
2. In Home Assistant, open **Settings → Devices & services → Add integration → Open Connex HA**.
3. Enter the controller address, such as `http://open-connex.local`, and its API token. Use the address without `/api/v1`; include a port if your controller uses one. The API token is separate from your web password.
4. Complete setup. Your zones appear automatically.

If the hostname does not resolve from Home Assistant, use the controller's IP address from your router. For HTTPS connections with a private certificate, see [connection settings](doc/usage.md#connection-settings).

The controller uses unencrypted HTTP. Keep it on a trusted local network and do not expose it directly to the Internet. Setup reads settings without sending heater commands.

## Everyday use

Each zone has a climate entity and an **Eco offset** control. The climate entity's `heat` state means the zone controls heating; it does not indicate that a heater is currently running.

**Normal** uses the base target. **Eco** shows the base target plus the Eco offset, matching the controller's dashboard. For example, a 21°C base target with a −3°C offset appears as 18°C in Eco. Setting 19°C in Eco changes the base target to 22°C; switching back to Normal then shows 22°C.

| Control | Range in the zone's unit |
| --- | --- |
| Normal target | 10–30°C or 50–86°F, in 0.5° steps |
| Eco target | The Normal range plus the Eco offset |
| Eco offset | −9 to +9, in whole degrees |

Settings save immediately. The controller waits **10 seconds after the last change to a zone** before transmitting. Repeated updates restart that delay, so avoid automations that continuously write the same value. Saved settings do not confirm delivery to a heater.

Create, rename, delete, and pair zones in the controller's web interface. **Visit device** opens it from Home Assistant. New zones are discovered within the normal 30-second polling interval. Removing the integration does not change controller settings or switch off heaters.

## Help and troubleshooting

- **A zone is unavailable:** check the controller's network connection, its health sensor, and whether it is in standby. Standby pauses transmissions; heaters can continue heating to their last setting.
- **Authentication failed:** copy the current API token from the controller's Security settings and enter it when Home Assistant asks you to authenticate again.
- **A temperature is rejected:** use a value on the zone's half-degree grid. Converting between Celsius and Fahrenheit can produce values outside that grid.

The [usage and troubleshooting guide](doc/usage.md) covers units, connection changes, standby, and uncertain command results.

For bugs or feature requests, [open an issue](https://github.com/fbuessen/open-connex-ha/issues). Include your Home Assistant, integration, and controller firmware versions, plus steps to reproduce the problem. Integration diagnostics can help; do not post API tokens, passwords, or unredacted configuration files.

## Development

The controller's [REST API reference](https://github.com/fbuessen/open-connex-controller/blob/main/doc/rest-api-spec.md) describes the interface used by this integration.

This is an independent project, not an official Dimplex integration.
