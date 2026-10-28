"""Batch claims: who is working on a batch.

A claim is the JSON file ``<batch_root>/batch_<id>/CLAIM`` with at least the key ``user``.
This module holds only what the per-batch driver needs: reading a claim and refusing to run a
batch that someone else has claimed.  Claiming, releasing and listing are added separately.
"""
from __future__ import annotations

import getpass
import json
from pathlib import Path

from .settings import Settings

CLAIM_FILE = "CLAIM"


class ClaimError(RuntimeError):
    pass


def claim_path(s: Settings, batch_id: str) -> Path:
    return Path(s.batch_root) / f"batch_{batch_id}" / CLAIM_FILE


def read_claim(s: Settings, batch_id: str) -> dict | None:
    """The claim of a batch, or None when the batch is unclaimed."""
    p = claim_path(s, batch_id)
    if not p.is_file():
        return None
    try:
        claim = json.loads(p.read_text())
    except ValueError as exc:
        raise ClaimError(f"{p}: claim file is not valid JSON ({exc})") from exc
    if not isinstance(claim, dict):
        raise ClaimError(f"{p}: claim file is not a JSON object")
    return claim


def require_own_claim(s: Settings, batch_id: str) -> None:
    """No claim: allowed.  A claim held by another user: ClaimError naming the owner."""
    claim = read_claim(s, batch_id)
    if claim is None:
        return
    owner = claim.get("user")
    me = getpass.getuser()
    if owner != me:
        raise ClaimError(f"batch {batch_id} is claimed by {owner!r} (you are {me!r}); "
                         f"ask them to release it")
