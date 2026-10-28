"""Site settings: where the raw archive, shards and batches live, and a few run options.

The file is TOML.  ``load_settings`` looks for it at an explicit path, else at
``$DGS_SETTINGS``, else at ``./settings.toml``.  Path values may contain ``$VAR`` and ``~``.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, fields
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on Python 3.10 only
    import tomli as tomllib

ENV_VAR = "DGS_SETTINGS"
DEFAULT_FILE = "settings.toml"


@dataclass(frozen=True)
class Settings:
    raw_root: Path
    shard_root: Path
    batch_root: Path
    staging: Path
    workers: int = 1
    keep_raw_after_verify: bool = True
    keep_shards_after_verify: bool = True
    with_resolution: bool = True
    reference_root: Path | None = None
    group: str | None = None


_PATH_KEYS = ("raw_root", "shard_root", "batch_root", "staging", "reference_root")
_REQUIRED = ("raw_root", "shard_root", "batch_root", "staging")


def _expand(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(value))))


def _locate(path: Path | None) -> Path:
    if path is not None:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"settings file not found: {p}")
        return p
    env = os.environ.get(ENV_VAR)
    if env:
        p = Path(env)
        if not p.is_file():
            raise FileNotFoundError(f"settings file not found: ${ENV_VAR}={env}")
        return p
    p = Path.cwd() / DEFAULT_FILE
    if p.is_file():
        return p
    raise FileNotFoundError(
        f"no settings file: pass a path, set ${ENV_VAR}, or create ./{DEFAULT_FILE} (looked for {p})"
    )


def load_settings(path: Path | None = None) -> Settings:
    """Read the settings TOML (explicit path, else $DGS_SETTINGS, else ./settings.toml)."""
    p = _locate(path)
    with open(p, "rb") as f:
        raw = tomllib.load(f)
    known = {f.name for f in fields(Settings)}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ValueError(f"{p}: unknown settings key(s): {', '.join(unknown)}")
    missing = [k for k in _REQUIRED if k not in raw]
    if missing:
        raise ValueError(f"{p}: missing required settings key(s): {', '.join(missing)}")
    for k in _PATH_KEYS:
        if raw.get(k) is not None:
            raw[k] = _expand(raw[k])
    return Settings(**raw)
