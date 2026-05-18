from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ProfileConfigError(Exception):
    pass


def _read_text_file(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def load_profile(profile_id: str, base_dir: Path | None = None) -> dict[str, Any]:
    repo_root = base_dir or Path(__file__).resolve().parents[2]
    profile_dir = repo_root / "profiles" / profile_id
    profile_path = profile_dir / "profile.json"

    if not profile_path.exists():
        raise ProfileConfigError(f"Профиль не найден: {profile_path}")

    raw = json.loads(profile_path.read_text(encoding="utf-8"))
    materials_cfg = raw.get("materials", {})

    materials = {}
    for key, rel_path in materials_cfg.items():
        materials[key] = _read_text_file(profile_dir / rel_path)

    raw["materials_text"] = materials
    raw["profile_dir"] = str(profile_dir)
    raw["calibration_dir"] = str(profile_dir / "calibration_examples")
    return raw


def load_calibration_examples(calibration_dir: str) -> list[dict[str, Any]]:
    path = Path(calibration_dir)
    if not path.exists():
        return []

    examples: list[dict[str, Any]] = []
    for file in sorted(path.glob("*.json")):
        try:
            examples.append(json.loads(file.read_text(encoding="utf-8")))
        except Exception:
            continue
    return examples

