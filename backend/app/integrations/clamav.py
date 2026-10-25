"""ClamAV clamd INSTREAM client. When CLAMD_HOST is unset, scanning is reported as unavailable."""

from __future__ import annotations

import socket
import struct

from ..config import get_settings


def scan_bytes(data: bytes) -> tuple[str, str | None]:
    """Return (status, detail) where status is clean | infected | not_scanned | error."""
    s = get_settings()
    if not s.clamd_host:
        return "not_scanned", "Malware scanner is not configured (set CLAMD_HOST)."
    try:
        with socket.create_connection((s.clamd_host, s.clamd_port), timeout=30) as sock:
            sock.sendall(b"zINSTREAM\0")
            view = memoryview(data)
            for i in range(0, len(data), 1 << 16):
                chunk = view[i : i + (1 << 16)]
                sock.sendall(struct.pack(">I", len(chunk)) + chunk)
            sock.sendall(struct.pack(">I", 0))
            reply = b""
            while not reply.endswith(b"\0"):
                part = sock.recv(4096)
                if not part:
                    break
                reply += part
        text = reply.rstrip(b"\0").decode("utf-8", "replace")
        if text.endswith("OK"):
            return "clean", "No malware signatures matched."
        if "FOUND" in text:
            return "infected", text.split(":", 1)[-1].strip()[:200]
        return "error", f"Unexpected scanner reply: {text[:120]}"
    except OSError as exc:
        return "error", f"Scanner unreachable: {type(exc).__name__}"


def ping() -> tuple[bool, str]:
    s = get_settings()
    if not s.clamd_host:
        return False, "CLAMD_HOST is not configured."
    try:
        with socket.create_connection((s.clamd_host, s.clamd_port), timeout=10) as sock:
            sock.sendall(b"zPING\0")
            reply = sock.recv(64)
        return reply.startswith(b"PONG"), reply.decode("utf-8", "replace").strip("\0")
    except OSError as exc:
        return False, f"Scanner unreachable: {type(exc).__name__}"
