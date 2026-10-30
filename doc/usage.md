# Usage and troubleshooting

## What Home Assistant shows

Each controller is a device in Home Assistant, with a child device for each zone. A zone groups all heaters paired to it; individual heaters are not listed.

The climate entity shows the requested temperature and Normal/Eco preset saved by the controller. It has no current-temperature reading or heating indicator because the heaters do not send that information. The `heat` state describes the available control mode. Turning a zone off, on, or toggling it is not supported.

The **Controller health** sensor reports whether the controller is ready to accept commands. It does not report heater connectivity.

Changing a device or entity name in Home Assistant only changes its local label. Manage zone names, pairing, and heater display units in the controller's web interface.

## Temperatures and Eco mode

In Normal mode, the displayed target is the base target. In Eco mode, it is the base target plus the Eco offset. Editing the Eco target adjusts the base target while keeping the offset. Changing the preset or offset preserves the base target.

An offset can be positive or negative. For example, a −3°C offset lowers the Eco target by three degrees. The available Eco range shifts by the same amount: a Celsius zone with that offset accepts an effective target from 7°C to 27°C.

Home Assistant displays climate temperatures in its configured system unit and converts commands to the zone's unit. A target must still land on a half-degree step in the zone's unit. For a Celsius zone, 68°F converts to 20°C and is accepted; 70°F converts to about 21.11°C and is rejected. Using the same temperature unit in Home Assistant and on the controller avoids this mismatch.

Eco offsets are temperature differences. A 1°C offset equals a 1.8°F offset. The offset entity defaults to the zone's unit and supports Home Assistant's per-entity display-unit preference, but a command must convert to a whole degree in the zone's unit.

When changing a zone's unit in the controller's web interface, pause automations for that zone and wait for Home Assistant to refresh before resuming. Avoid editing the same zone from both interfaces at once. If Home Assistant detects a unit change during a command, it refreshes the settings and asks you to try again.

## Updates and automations

Home Assistant checks the controller every 30 seconds and discovers new zones automatically. Updates made in the web interface appear on the next successful refresh. A removed zone becomes unavailable; you can remove its unused device from Home Assistant once the inventory has refreshed.

The controller saves accepted changes immediately, then waits for 10 seconds without another update to that zone. It transmits the latest settings five times, one second apart. Even an unchanged value restarts this delay. Configure automations to send a value when it needs to change, rather than continuously repeating it.

The controller performs an hourly refresh of its own. Home Assistant does not periodically resend settings or restore them to the controller after a restart.

## Connection settings

Open the integration's menu in **Settings → Devices & services** and choose **Reconfigure** to change its address, API token, or trusted CA certificate. Reconfiguration keeps the same controller identity. Re-enter the API token when saving; the password field is never prefilled.

Use the controller's address without an API path. The usual address is `http://open-connex.local`; an IP address or a nonstandard port also works. Home Assistant and the controller must be able to reach each other on your network.

The standard controller firmware serves HTTP. If your endpoint provides HTTPS, certificate verification is always enabled. For a private certificate authority, paste its PEM CA certificate into **Trusted CA certificate**. The server certificate must be valid for the hostname you enter and must not be expired. Leave the CA field blank to use the usual trusted authorities. Do not paste a private key into this field.

An API token is required for every controller request. Generate, copy, rotate, or revoke it in the controller's **Settings → Security**. Web passwords and browser cookies cannot replace an API token. Rotation or revocation causes Home Assistant to request new credentials; previous commands are not repeated.

## Problems and recovery

### The controller or a zone is unavailable

Check that the controller's web interface opens from the same network as Home Assistant. If its address changed, use **Reconfigure**. Following read failures, polling slows down to a maximum of five minutes and returns to 30 seconds after recovery.

If **Controller health** shows **Degraded**, the controller may be in standby or have a local fault. The API does not distinguish these causes. A short press of the controller's button wakes it from standby. Standby stops transmissions but does not switch off the heaters. It cancels pending sends and pairing; waking does not replay canceled work. There is no standby control in Home Assistant.

If only one zone is unavailable, check whether it still exists on the controller. A failed read after a command can also leave that zone unavailable until its settings can be refreshed.

### A command was rejected

Check the zone's displayed temperature limits and selected unit. Wait for active pairing to finish before changing its settings. A busy or degraded controller can reject commands without saving them. Home Assistant does not retry control requests automatically.

### The command's result is unknown

A connection can fail after the controller receives a command but before Home Assistant receives the response. Home Assistant then reads the saved settings without repeating the command. Check the controller's web interface and the heater before deliberately sending it again.

If the controller accepted a command but the follow-up read failed, the command remains accepted. The zone becomes unavailable until a read succeeds. Acceptance means that transmission was scheduled; it does not confirm heater behavior.

### The controller identity changed

An address may be reassigned to a different controller. Home Assistant stops control and raises a repair issue in that case. Reconfigure with the original controller's address, or add the replacement controller as a separate integration.

### Reporting a problem

Include the Home Assistant Core version, integration version, controller firmware version, and the steps that caused the problem when [opening an issue](https://github.com/fbuessen/open-connex-ha/issues).

You can download integration diagnostics from Home Assistant. They include firmware version, controller health, zone counts, and error codes; they exclude tokens, addresses, controller and zone identities, names, and raw API responses. Review any other logs or screenshots before sharing them, and remove credentials and personal information.
