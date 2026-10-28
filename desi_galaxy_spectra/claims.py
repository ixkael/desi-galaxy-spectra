"""Batch claims and the permissions check: several users share one batch queue.

A claim is the JSON file ``<batch_root>/batch_<id>/CLAIM`` with the keys ``user``, ``host``,
``slurm_job_id`` and ``time``.  It is created exclusively (``O_CREAT | O_EXCL``), so when two
users claim the same batch at the same moment exactly one of them succeeds.  ``release`` removes
the claim; ``release(force=True)`` removes someone else's claim and appends a line to
``<batch_root>/claims.log`` (time, who forced it, the claim that was removed).

``doctor`` lists the files and directories of the three data roots that a collaborator in the
shared group could not write to.  It only reports; it never changes a permission.
"""
from __future__ import annotations

import datetime
import getpass
import json
import os
import socket
import stat
import sys
from pathlib import Path

from .batches import batch_ids
from .settings import Settings

CLAIM_FILE = "CLAIM"
CLAIMS_LOG = "claims.log"


class ClaimError(RuntimeError):
    pass


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def claim_path(s: Settings, batch_id: str) -> Path:
    return Path(s.batch_root) / f"batch_{batch_id}" / CLAIM_FILE


def read_claim(s: Settings, batch_id: str) -> dict | None:
    """The claim of a batch, or None when the batch is unclaimed."""
    p = claim_path(s, batch_id)
    if not p.is_file():
        return None
    try:
        claim = json.loads(p.read_text())
    except OSError as exc:
        raise ClaimError(f"{p}: claim file cannot be read ({exc.strerror})") from exc
    except ValueError as exc:
        raise ClaimError(f"{p}: claim file is empty or not valid JSON ({exc})") from exc
    if not isinstance(claim, dict):
        raise ClaimError(f"{p}: claim file is not a JSON object")
    return claim


def _raw_claim(p: Path) -> bytes | str:
    """The bytes of an unreadable claim file, for the log (or a note when even that fails)."""
    try:
        return p.read_bytes()
    except OSError as exc:
        return f"<cannot read: {exc.strerror}>"


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


def claim(s: Settings, batch_id: str) -> dict:
    """Claim a batch for the current user and return the claim.  ClaimError if the batch is
    already claimed (by anyone, including you)."""
    p = claim_path(s, batch_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"user": getpass.getuser(), "host": socket.gethostname(),
           "slurm_job_id": os.environ.get("SLURM_JOB_ID"), "time": _now()}
    # The content is written to a temporary file first and the file is then hard-linked to CLAIM
    # (os.link fails if CLAIM exists), so CLAIM never exists empty or half-written.
    tmp = p.parent / f".{CLAIM_FILE}.{os.getpid()}.{os.urandom(4).hex()}.tmp"
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)    # mode 664 under umask 002
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(rec) + "\n")
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, p)
        except FileExistsError:
            try:
                owner = (read_claim(s, batch_id) or {}).get("user", "?")
            except ClaimError:
                owner = "?"
            who = "you" if owner == rec["user"] else repr(owner)
            raise ClaimError(f"batch {batch_id} is already claimed by {who}") from None
    finally:
        tmp.unlink(missing_ok=True)
    return rec


def release(s: Settings, batch_id: str, force: bool = False, log=print) -> None:
    """Remove the claim of a batch.  Without ``force`` only the owner may do it.  With ``force``
    a line (time, who forced it, the previous claim) is appended to ``claims.log`` first.  With
    ``force`` an unreadable claim (empty, not JSON) is accepted: its raw bytes are logged and the
    file removed.  Without ``force`` an unreadable claim is a ClaimError."""
    p = claim_path(s, batch_id)
    me = getpass.getuser()
    try:
        prev = read_claim(s, batch_id)
        previous = None if prev is None else f"previous claim {json.dumps(prev, sort_keys=True)}"
    except ClaimError as exc:
        if not force:
            raise ClaimError(f"{exc}; remove it with release --force") from exc
        prev = {}
        previous = f"previous claim UNREADABLE ({exc}); raw bytes {_raw_claim(p)!r}"
    if previous is None:
        log(f"batch {batch_id}: not claimed")
        return
    if prev.get("user") != me:
        if not force:
            raise ClaimError(f"batch {batch_id} is claimed by {prev.get('user')!r} (you are "
                             f"{me!r}); only they can release it, or use --force")
    if force:
        logf = Path(s.batch_root) / CLAIMS_LOG
        logf.parent.mkdir(parents=True, exist_ok=True)
        with open(logf, "a") as f:
            f.write(f"{_now()}\tbatch {batch_id}\tforced release by {me}\t{previous}\n")
    p.unlink(missing_ok=True)
    log(f"batch {batch_id}: released" + (" (forced, recorded in claims.log)" if force else ""))


def next_unclaimed(s: Settings) -> str:
    """The lowest of batches 000-083 that has no claim and no DELIVERED marker.  A batch whose
    claim file is unreadable counts as claimed (not offered); a warning naming the file goes to
    stderr, and ``release --force`` clears it."""
    from .status import DELIVERED       # status imports this module
    for b in batch_ids(include_ref22=False):
        out = Path(s.batch_root) / f"batch_{b}"
        try:
            held = read_claim(s, b) is not None
        except ClaimError as exc:
            print(f"warning: batch {b} treated as claimed: {exc}", file=sys.stderr, flush=True)
            continue
        if not held and not (out / DELIVERED).is_file():
            return b
    raise ClaimError("every batch is claimed or delivered")


def unreadable_claims(s: Settings) -> list[str]:
    """One message per claim file (any batch, including ref22) that cannot be parsed."""
    out = []
    for b in batch_ids(include_ref22=True):
        try:
            read_claim(s, b)
        except ClaimError as exc:
            out.append(f"{claim_path(s, b)}: unreadable claim ({exc}); "
                       f"clear it with: dgs release --force --batch {b}")
    return out


def _group_name(gid: int) -> str:
    try:
        import grp
        return grp.getgrgid(gid).gr_name
    except (ImportError, KeyError):
        return str(gid)


def doctor(s: Settings) -> list[str]:
    """Problems found under raw_root, shard_root and batch_root, one message per path: not
    group-writable; group differs from ``s.group`` (when set); directory without setgid.
    Symbolic links are skipped.  Nothing is changed."""
    problems: list[str] = []

    def check(path: Path) -> None:
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode):
            return
        why = []
        if not st.st_mode & stat.S_IWGRP:
            why.append("not group-writable")
        if s.group and _group_name(st.st_gid) != s.group:
            why.append(f"group is {_group_name(st.st_gid)!r}, not {s.group!r}")
        if stat.S_ISDIR(st.st_mode) and not st.st_mode & stat.S_ISGID:
            why.append("directory without setgid")
        if why:
            problems.append(f"{path}: {'; '.join(why)} (mode {stat.S_IMODE(st.st_mode):04o})")

    for root in (s.raw_root, s.shard_root, s.batch_root):
        root = Path(root)
        if not root.is_dir():
            continue
        check(root)
        for dirpath, dirnames, filenames in os.walk(
                root, onerror=lambda e: problems.append(f"{e.filename}: unreadable ({e.strerror})")):
            for name in sorted(dirnames) + sorted(filenames):
                check(Path(dirpath) / name)
    problems.extend(unreadable_claims(s))
    return problems
