from __future__ import annotations

import os
from pathlib import Path


def _load_project_dotenv() -> None:
    """Load KEY=VALUE pairs from repo-root .env without overriding existing env."""
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if raw.lower().startswith("export "):
            raw = raw[7:].strip()
        if "=" not in raw:
            continue

        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue

        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]

        os.environ[key] = value


_load_project_dotenv()

__all__ = ["__version__"]
__version__ = "0.1.0"
