"""Credential resolution for integrations.

Credentials come either from an environment variable named on the integration
(recommended) or from a value entered in the settings panel, which is stored
encrypted with SECRETS_ENCRYPTION_KEY. Plaintext secrets are never returned by the API.
"""

from __future__ import annotations

import os
import re

from ..models import Integration
from ..security.crypto import decrypt_secret

ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,99}$")


def resolve_secret(integration: Integration) -> str | None:
    if integration.secret_env_var:
        value = os.environ.get(integration.secret_env_var)
        if value:
            return value
    if integration.secret_enc:
        try:
            return decrypt_secret(integration.secret_enc)
        except Exception:  # noqa: BLE001 - wrong key / corrupted value
            return None
    return None


def secret_status(integration: Integration) -> dict:
    env_present = bool(integration.secret_env_var and os.environ.get(integration.secret_env_var))
    stored = bool(integration.secret_enc)
    if integration.auth_method == "none":
        source = "not_required"
    elif env_present:
        source = "environment"
    elif stored:
        source = "encrypted_store"
    else:
        source = "missing"
    return {"source": source, "env_var": integration.secret_env_var, "env_present": env_present, "stored": stored}
