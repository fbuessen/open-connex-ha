"""Private CA trust never disables certificate or hostname verification."""

import ipaddress
import ssl
from datetime import UTC, datetime, timedelta

import pytest
from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from custom_components.open_connex_ha import async_create_client
from custom_components.open_connex_ha.api import ApiError

from .conftest import DEVICE


@pytest.fixture
def rejected_tls_handshakes(hass):
    """The local server sees expected resets when clients reject its certificate."""
    previous = hass.loop.get_exception_handler()

    def handler(loop, context):
        if context.get(
            "message"
        ) == "Error on transport creation for incoming connection" and isinstance(
            context.get("exception"), ConnectionResetError
        ):
            return
        if previous:
            previous(loop, context)
        else:
            loop.default_exception_handler(context)

    hass.loop.set_exception_handler(handler)
    yield
    hass.loop.set_exception_handler(previous)


@pytest.mark.parametrize("kind", ["valid", "wrong_host", "expired"])
async def test_verified_private_certificate(
    hass, rejected_tls_handshakes, aiohttp_server, socket_enabled, tmp_path, kind
):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Connex test")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=2))
        .not_valid_after(now + timedelta(days=1) if kind != "expired" else now - timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.IPAddress(
                        ipaddress.ip_address("127.0.0.2" if kind == "wrong_host" else "127.0.0.1")
                    )
                ]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    pem = certificate.public_bytes(serialization.Encoding.PEM)
    cert_file = tmp_path / "test.crt"
    key_file = tmp_path / "test.key"
    cert_file.write_bytes(pem)
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)
    app = web.Application()

    async def device(request):
        assert request.headers["Authorization"] == "Bearer test-token"
        return web.json_response(DEVICE)

    app.router.add_get("/api/v1/device", device)
    server = await aiohttp_server(app, ssl=context)
    data = {"origin": str(server.make_url("/")).rstrip("/"), "token": "test-token"}
    untrusted = await async_create_client(hass, data)
    with pytest.raises(ApiError, match="cannot_connect"):
        await untrusted.get_device()
    trusted = await async_create_client(hass, {**data, "ca_certificate": pem.decode()})
    if kind == "valid":
        assert (await trusted.get_device()).id == DEVICE["id"]
    else:
        with pytest.raises(ApiError, match="cannot_connect"):
            await trusted.get_device()
