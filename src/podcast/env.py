"""Small helper for required environment variables (secrets via .env only)."""

from __future__ import annotations

import os


def require_env(name: str) -> str:
    """Return os.environ[name], or raise a clear error if it's missing/empty."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set — add it to .env before running this stage")
    return value
