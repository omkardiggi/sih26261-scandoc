"""Outbound HTTP for integrations with SSRF protection.

* HTTPS only; no credentials in URLs; redirects are never followed.
* Hostnames are resolved and private, loopback, link-local, multicast and
  reserved addresses are refused (also re-checked against the connected peer).
* Response size and time are capped.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from ..config import get_settings

# Tests can inject an httpx.MockTransport here; never set in production code paths.
transport_override: httpx.BaseTransport | None = None


class OutboundBlocked(Exception):
    """The destination is not permitted (SSRF protection)."""


class OutboundError(Exception):
    """Network-level failure (DNS, TLS, timeout, connection)."""


@dataclass
class OutboundResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes
    duration_ms: int
    url: str
    truncated: bool = False
    meta: dict = field(default_factory=dict)

    def json(self):
        import json

        return json.loads(self.body.decode("utf-8"))


def _ip_forbidden(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
        or (isinstance(addr, ipaddress.IPv4Address) and addr in ipaddress.ip_network("100.64.0.0/10"))
    )


OFFICIAL_SUFFIXES = (".gov.in", ".nic.in")


def _resolves_to_own_network(host: str, port: int) -> bool:
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    return bool(infos) and all(_ip_forbidden(i[4][0]) for i in infos)


def validate_destination(url: str) -> str:
    """Return the normalised URL or raise OutboundBlocked."""
    parts = urlsplit(url)
    s = get_settings()
    if s.private_mode and transport_override is None:
        host = (parts.hostname or "").lower()
        official = s.allow_official_lookups and host.endswith(OFFICIAL_SUFFIXES)
        if not official:
            if host and parts.scheme in ("http", "https") and _resolves_to_own_network(host, parts.port or 443):
                return url  # a service on this machine or this organisation's own network
            raise OutboundBlocked("Private mode: requests to outside servers are disabled on this installation.")
    if parts.scheme != "https":
        raise OutboundBlocked("Only HTTPS destinations are permitted.")
    if not parts.hostname:
        raise OutboundBlocked("The URL has no host.")
    if parts.username or parts.password:
        raise OutboundBlocked("Credentials must not be embedded in URLs.")
    if parts.port not in (None, 443, 8443):
        raise OutboundBlocked("Only ports 443 and 8443 are permitted.")
    if get_settings().allow_private_integration_hosts or transport_override is not None:
        return url
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise OutboundError(f"DNS resolution failed for {parts.hostname}") from exc
    for info in infos:
        if _ip_forbidden(info[4][0]):
            raise OutboundBlocked("The destination resolves to a private or reserved network address.")
    return url


def request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict | None = None,
    content: bytes | str | None = None,
    json_body: dict | None = None,
    timeout: float | None = None,
    max_bytes: int | None = None,
) -> OutboundResponse:
    s = get_settings()
    validate_destination(url)
    limit = max_bytes or s.integration_max_response_bytes
    started = time.monotonic()
    client_kwargs: dict = {"follow_redirects": False, "timeout": timeout or s.integration_timeout_seconds}
    if transport_override is not None:
        client_kwargs["transport"] = transport_override
    try:
        with httpx.Client(**client_kwargs) as client:
            with client.stream(method, url, headers=headers, params=params, content=content, json=json_body) as resp:
                stream = resp.extensions.get("network_stream")
                if stream is not None and not s.allow_private_integration_hosts:
                    peer = stream.get_extra_info("server_addr")
                    if peer and _ip_forbidden(peer[0]):
                        raise OutboundBlocked("Connected peer is a private or reserved address.")
                chunks: list[bytes] = []
                size = 0
                truncated = False
                for chunk in resp.iter_bytes():
                    size += len(chunk)
                    if size > limit:
                        truncated = True
                        break
                    chunks.append(chunk)
                body = b"".join(chunks)
                return OutboundResponse(
                    status_code=resp.status_code,
                    headers={k.lower(): v for k, v in resp.headers.items()},
                    body=body,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    url=str(resp.url.copy_with(query=None)),
                    truncated=truncated,
                )
    except OutboundBlocked:
        raise
    except httpx.TimeoutException as exc:
        raise OutboundError("The request timed out.") from exc
    except httpx.HTTPError as exc:
        raise OutboundError(f"Network error: {type(exc).__name__}") from exc
