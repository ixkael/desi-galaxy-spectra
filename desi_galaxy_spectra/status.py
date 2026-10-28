"""Where a batch stands, derived from markers on disk, and the table over all batches.

The stage of a batch is never read from ``status.json``; it is recomputed from the files:

  downloaded  every pixel has the SHA256_OK marker in the bright and the dark raw directory
  extracted   every pixel has its shard ``healpix_<pix>.h5``
  packaged    ``manifest.json`` exists and lists exactly the chunk files on disk
  verified    ``VERIFY.md`` has the line ``RESULT: PASS`` and ``MD5SUMS.txt`` rechecks
  delivered   ``DELIVERED`` exists and the batch is verified

The stage is the highest one whose condition and all lower conditions hold.  One exception
keeps a finished batch finished after its raw files or shards were deleted on purpose: the
driver then leaves the marker ``RAW_REMOVED`` or ``SHARDS_REMOVED`` in the batch directory, and
that lower condition is taken as met only while the batch is still packaged.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import h5py

from . import claims, config, manifest
from .batches import batch_ids, read_batch
from .package import md5_file
from .settings import Settings

STAGES = ("queued", "downloaded", "extracted", "packaged", "verified", "delivered")

RAW_REMOVED = "RAW_REMOVED"
SHARDS_REMOVED = "SHARDS_REMOVED"
DELIVERED = "DELIVERED"
CLASS_NAMES = ("BGS", "ELG", "LRG")

_FILE_RE = re.compile(r"^FILE (\S+) (\d+)$")


def batch_dirs(s: Settings, batch_id: str) -> tuple[Path, Path]:
    """(shard directory, output directory) of a batch."""
    return (Path(s.shard_root) / f"batch_{batch_id}", Path(s.batch_root) / f"batch_{batch_id}")


def raw_dirs(s: Settings, pixels) -> list[Path]:
    return [config.raw_dir(s.raw_root, p, prog) for p in pixels for prog in config.PROGRAMS]


def chunk_names(out: Path) -> set[str]:
    """Relative names of the HDF5 files in a batch output directory."""
    out = Path(out)
    return {str(p.relative_to(out)) for p in manifest.list_h5(out)} if out.is_dir() else set()


def parse_md5sums(path: Path) -> dict[str, str]:
    """{name: md5} from an `md5sum` listing."""
    sums = {}
    for line in Path(path).read_text().splitlines():
        if line.strip():
            digest, _, name = line.partition("  ")
            sums[name.strip()] = digest.strip()
    return sums


def _raw_ok(s, pixels) -> bool:
    return all((d / config.SHA_MARKER).is_file() for d in raw_dirs(s, pixels))


def _shards_ok(shard_dir: Path, pixels) -> bool:
    return all((shard_dir / config.shard_name(p)).is_file() for p in pixels)


def _packaged_ok(out: Path) -> bool:
    mp = out / "manifest.json"
    if not mp.is_file():
        return False
    try:
        listed = set(manifest.load_manifest(mp)["files"])
    except (ValueError, KeyError, OSError):
        return False
    on_disk = chunk_names(out)
    return bool(on_disk) and listed == on_disk


def _verified_ok(out: Path, deep: bool) -> bool:
    vm, mm = out / "VERIFY.md", out / "MD5SUMS.txt"
    if not (vm.is_file() and mm.is_file()):
        return False
    vlines = vm.read_text().splitlines()
    if "RESULT: PASS" not in vlines:
        return False
    try:
        sums = parse_md5sums(mm)
    except OSError:
        return False
    if set(sums) != chunk_names(out) | {"manifest.json"}:
        return False
    if deep:
        return all(md5_file(out / n) == m for n, m in sums.items())
    sizes = {m.group(1): int(m.group(2)) for ln in vlines if (m := _FILE_RE.match(ln))}
    return all(sizes.get(n) == (out / n).stat().st_size for n in sums)


def stage_on_disk(s: Settings, batch_id: str, deep: bool = True) -> str:
    """The stage of a batch from its markers.  ``deep=False`` replaces the md5 recheck of the
    chunk files by a comparison of their sizes with those recorded in VERIFY.md (for status
    tables over many batches); the driver always uses the full recheck."""
    pixels = read_batch(batch_id)
    shard_dir, out = batch_dirs(s, batch_id)
    packaged = _packaged_ok(out)
    if not (_raw_ok(s, pixels) or (packaged and (out / RAW_REMOVED).is_file())):
        return "queued"
    if not (_shards_ok(shard_dir, pixels) or (packaged and (out / SHARDS_REMOVED).is_file())):
        return "downloaded"
    if not packaged:
        return "extracted"
    if not _verified_ok(out, deep):
        return "packaged"
    if (out / DELIVERED).is_file():
        return "delivered"
    return "verified"


def chunk_counts(out: Path) -> dict:
    """Counts and bytes of the chunk files of a batch, from their root attributes."""
    out = Path(out)
    files = sorted(manifest.list_h5(out)) if out.is_dir() else []
    n = {c: 0 for c in CLASS_NAMES}
    total = 0
    for p in files:
        with h5py.File(p, "r") as f:
            for c in CLASS_NAMES:
                n[c] += int(f.attrs.get("n_" + c, 0))
            total += int(f.attrs["n_objects"])
    return {"n_chunk_files": len(files), "n_BGS": n["BGS"], "n_ELG": n["ELG"],
            "n_LRG": n["LRG"], "n_clean": total, "bytes": sum(p.stat().st_size for p in files)}


def dir_bytes(paths) -> int:
    total = 0
    for d in paths:
        if Path(d).is_dir():
            for root, _, names in os.walk(d):
                total += sum(os.path.getsize(os.path.join(root, n)) for n in names)
    return total


def status_table(s: Settings, ids: list[str] | None = None, deep: bool = False) -> list[dict]:
    """One row per batch: batch, stage, claimed_by, n_pixels, n_clean, bytes.

    ``n_clean`` and ``bytes`` come from the chunk files (None when a chunk file is unreadable).
    """
    rows = []
    for b in (ids if ids is not None else batch_ids()):
        _, out = batch_dirs(s, b)
        claim = claims.read_claim(s, b)
        try:
            cc = chunk_counts(out)
            n_clean, nbytes = cc["n_clean"], cc["bytes"]
        except (OSError, KeyError):
            n_clean, nbytes = None, None
        rows.append({"batch": b, "stage": stage_on_disk(s, b, deep=deep),
                     "claimed_by": (claim or {}).get("user", ""),
                     "n_pixels": len(read_batch(b)), "n_clean": n_clean, "bytes": nbytes})
    return rows


COLUMNS = ("batch", "stage", "claimed_by", "n_pixels", "n_clean", "bytes")


def _cell(v) -> str:
    return "unreadable" if v is None else str(v)


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def write_status(s: Settings, ids: list[str] | None = None, deep: bool = False) -> tuple[Path, Path]:
    """Write ``status.tsv`` and ``status.md`` next to the batch root."""
    rows = status_table(s, ids, deep=deep)
    base = Path(s.batch_root).parent
    base.mkdir(parents=True, exist_ok=True)
    tsv, md = base / "status.tsv", base / "status.md"
    _atomic_write(tsv, "\t".join(COLUMNS) + "\n"
                  + "".join("\t".join(_cell(r[c]) for c in COLUMNS) + "\n" for r in rows))
    counts = {st: sum(1 for r in rows if r["stage"] == st) for st in STAGES}
    lines = ["# Batch status", "",
             "Stages are derived from the files on disk. "
             + ("Chunk files were re-hashed." if deep else
                "Chunk files were compared by size with VERIFY.md, not re-hashed."), "",
             "Batches per stage: " + ", ".join(f"{st} {counts[st]}" for st in STAGES), "",
             "| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    lines += ["| " + " | ".join(_cell(r[c]) for c in COLUMNS) + " |" for r in rows]
    _atomic_write(md, "\n".join(lines) + "\n")
    return tsv, md
