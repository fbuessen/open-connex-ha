"""A local HTTP controller fake and real Home Assistant integration fixtures."""

from collections import defaultdict, deque
from copy import deepcopy

import pytest
from aiohttp import web
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.open_connex_ha.const import DOMAIN

DEVICE = {"id": "controller_test", "firmware_version": "0.2.9", "health": "ok"}
ZONE = {
    "id": "zone_test",
    "name": "Living room",
    "configuration": {
        "target_temperature": 21.0,
        "mode": "normal",
        "eco_offset": 0,
        "unit": "celsius",
    },
}


class FakeController:
    def __init__(self):
        self.device = deepcopy(DEVICE)
        self.zones = {ZONE["id"]: deepcopy(ZONE)}
        self.requests = []
        self.authorization_headers = []
        self.patches = []
        self.faults = defaultdict(deque)
        self.origin = ""
        self.token = "test-secret-token"

    async def handle(self, request):
        response = await self._handle(request)
        # The ESP32 serves one request per connection, including successful reads.
        response.headers["Connection"] = "close"
        response.headers["Cache-Control"] = "no-store"
        response.force_close()
        return response

    async def _handle(self, request):
        body = await request.json() if request.method == "PATCH" else None
        self.requests.append((request.method, request.raw_path, body))
        authorization = request.headers.get("Authorization")
        self.authorization_headers.append(authorization)
        if not self.token or authorization != f"Bearer {self.token}":
            return web.json_response({"error": {"code": "invalid_auth"}}, status=401)
        key = (request.method, request.path)
        if self.faults[key]:
            fault = self.faults[key].popleft()
            return await fault(request) if callable(fault) else fault
        if request.path == "/api/v1/device":
            return web.json_response(self.device)
        if request.path == "/api/v1/zones":
            return web.json_response({"zones": list(self.zones.values())})
        zone_id = request.match_info["zone_id"]
        if zone_id not in self.zones:
            return web.json_response({"error": {"code": "not_found"}}, status=404)
        if request.method == "PATCH":
            if self.device["health"] != "ok":
                return web.json_response({"error": {"code": "unavailable"}}, status=503)
            if "eco_offset" in body and type(body["eco_offset"]) is not int:
                return web.json_response({"error": {"code": "invalid_field"}}, status=422)
            self.patches.append(deepcopy(body))
            self.zones[zone_id]["configuration"].update(body)
            return web.Response(status=202)
        return web.json_response(self.zones[zone_id])


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    """Load custom_components from this development tree."""


@pytest.fixture
async def controller(aiohttp_server, socket_enabled):
    fake = FakeController()
    app = web.Application()
    app.router.add_route("GET", "/api/v1/device", fake.handle)
    app.router.add_route("GET", "/api/v1/zones", fake.handle)
    app.router.add_route("*", "/api/v1/zones/{zone_id:.+}", fake.handle)
    server = await aiohttp_server(app)
    fake.origin = str(server.make_url("/")).rstrip("/")
    return fake


@pytest.fixture
def entry(controller):
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=controller.device["id"],
        title="Connex controller",
        data={"origin": controller.origin, "token": controller.token},
    )


@pytest.fixture
async def integration(hass, controller, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    if hasattr(entry, "runtime_data") and not entry.runtime_data.coordinator.closing:
        await hass.config_entries.async_unload(entry.entry_id)
