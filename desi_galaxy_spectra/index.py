"""The file index over all batches and the cross-batch uniqueness check."""
from __future__ import annotations

import os
from pathlib import Path

import h5py
import numpy as np

from . import manifest
from .settings import Settings
from .status import CLASS_NAMES, parse_md5sums, stage_on_disk

INDEX_COLUMNS = ("batch", "file", "n_BGS", "n_ELG", "n_LRG", "bytes", "md5")


def _batch_dirs(s: Settings) -> list[Path]:
    """Output directories of the batches that are verified or delivered (stage read from the
    files; chunk sizes against VERIFY.md, not re-hashed).  Batches that failed verification or
    have not been verified have chunk files and an MD5SUMS.txt but belong in neither the index
    nor the uniqueness check."""
    root = Path(s.batch_root)
    if not root.is_dir():
        return []
    dirs = sorted(p for p in root.glob("batch_*") if p.is_dir())
    return [p for p in dirs
            if stage_on_disk(s, p.name[len("batch_"):], deep=False) in ("verified", "delivered")]


def build_index(s: Settings) -> Path:
    """Write ``index.csv`` (batch, file, n_BGS, n_ELG, n_LRG, bytes, md5) next to the batch root.

    One row per chunk file of every verified or delivered batch; the md5 is the one recorded in
    its ``MD5SUMS.txt``.  A chunk file absent because its redshift range is empty has no row.
    """
    base = Path(s.batch_root).parent
    base.mkdir(parents=True, exist_ok=True)
    lines = [",".join(INDEX_COLUMNS)]
    for d in _batch_dirs(s):
        sums_path = d / "MD5SUMS.txt"
        if not sums_path.is_file():
            continue
        sums = parse_md5sums(sums_path)
        bid = d.name[len("batch_"):]
        for p in sorted(manifest.list_h5(d)):
            rel = str(p.relative_to(d))
            if rel not in sums:
                raise ValueError(f"{sums_path}: no md5 for chunk file {rel}")
            with h5py.File(p, "r") as f:
                n = [int(f.attrs.get("n_" + c, 0)) for c in CLASS_NAMES]
            lines.append(",".join([bid, rel, *map(str, n), str(p.stat().st_size), sums[rel]]))
    out = base / "index.csv"
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.replace(tmp, out)
    return out


def check_unique(s: Settings) -> list[str]:
    """Messages for every (targetid, program) that occurs more than once across all chunk files
    of all verified or delivered batches (empty list: all unique).  Only ``catalog/targetid`` and ``catalog/program``
    of each class group are read."""
    tids, progs, where = [], [], []
    names: list[str] = []
    for d in _batch_dirs(s):
        for p in sorted(manifest.list_h5(d)):
            label = f"{d.name}/{p.relative_to(d)}"
            with h5py.File(p, "r") as f:
                for c in CLASS_NAMES:
                    if c not in f:
                        continue
                    t = f[c]["catalog/targetid"][()].astype("i8")
                    pr = f[c]["catalog/program"][()]
                    tids.append(t)
                    progs.append(pr)
                    where.append(np.full(len(t), len(names), dtype="i8"))
            names.append(label)
    if not tids:
        return []
    tid, prog, wh = np.concatenate(tids), np.concatenate(progs), np.concatenate(where)
    _, code = np.unique(prog, return_inverse=True)
    order = np.lexsort((code, tid))
    tid, code, wh = tid[order], code[order], wh[order]
    same = (tid[1:] == tid[:-1]) & (code[1:] == code[:-1])
    msgs = []
    for i in np.nonzero(same)[0].tolist():
        pname = prog[order[i]]
        pname = pname.decode() if isinstance(pname, bytes) else str(pname)
        msgs.append(f"(targetid, program) ({int(tid[i])}, {pname}) is in both "
                    f"{names[wh[i]]} and {names[wh[i + 1]]}")
    return msgs
