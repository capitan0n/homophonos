"""Persistence for saved sink-combination profiles.

Stored as plain JSON under XDG_CONFIG_HOME so users can inspect or
hand-edit it if needed. No third-party YAML dependency is pulled in
just for this -- JSON is stdlib, human-readable enough, and avoids the
extra install step on a fresh Manjaro system.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pwcombine.models import Profile


class ConfigError(RuntimeError):
    """Raised when the profile store can't be read or written."""


def _config_dir() -> Path:
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config) if xdg_config else Path.home() / ".config"
    return base / "pw-combine"


def _config_path() -> Path:
    return _config_dir() / "profiles.json"


def load_profiles() -> dict[str, Profile]:
    """Load all saved profiles, keyed by name.

    Returns an empty dict if the file doesn't exist yet -- that's the
    normal first-run state, not an error.
    """
    path = _config_path()
    if not path.exists():
        return {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Could not read profile store at {path}: {exc}") from exc

    profiles: dict[str, Profile] = {}
    for entry in raw.get("profiles", []):
        try:
            profile = Profile.from_dict(entry)
        except (KeyError, TypeError):
            # A single corrupted entry shouldn't make every other
            # saved profile inaccessible.
            continue
        profiles[profile.name] = profile

    return profiles


def save_profile(profile: Profile) -> None:
    """Add or overwrite one profile, preserving all others."""
    profiles = load_profiles()
    profiles[profile.name] = profile
    _write_all(profiles)


def delete_profile(name: str) -> bool:
    """Remove a profile by name. Returns False if it didn't exist."""
    profiles = load_profiles()
    if name not in profiles:
        return False
    del profiles[name]
    _write_all(profiles)
    return True


def _write_all(profiles: dict[str, Profile]) -> None:
    path = _config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"profiles": [p.to_dict() for p in profiles.values()]}
        # Write to a temp file then replace atomically, so a crash or
        # power loss mid-write can't corrupt an otherwise-good file.
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(path)
    except OSError as exc:
        raise ConfigError(f"Could not write profile store at {path}: {exc}") from exc
