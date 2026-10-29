"""Content manifest of a directory of HDF5 files.

{kit_version, lockfile_sha256, created, files: {relpath: {datasets: {h5path: {dtype, shape, md5}},
attrs_md5}}}.  Dataset md5 is over the raw C-order array bytes, streamed in row blocks;
attrs_md5 is over all attributes of all objects (sorted), excluding timestamps.
"""
from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path

import h5py
import numpy as np

from . import config
from .parallel import pmap

TIMESTAMP_ATTRS = frozenset({"created", "creation_date"})
BLOCK_BYTES = 64 * 1024 * 1024


def _row_blocks(ds):
    if ds.shape == () or ds.shape[0] == 0:
        yield ds[()] if ds.shape == () else np.empty(ds.shape, ds.dtype)
        return
    row_bytes = max(1, int(np.prod(ds.shape[1:], dtype=np.int64)) * ds.dtype.itemsize)
    step = max(1, BLOCK_BYTES // row_bytes)
    for s in range(0, ds.shape[0], step):
        yield ds[s:s + step]


def dataset_md5(ds) -> str:
    h = hashlib.md5()
    for blk in _row_blocks(ds):
        a = np.asarray(blk)
        if a.dtype.kind == "O":
            h.update(json.dumps(np.asarray(a).tolist(), default=_jd).encode())
        else:
            h.update(np.ascontiguousarray(a).tobytes())
    return h.hexdigest()


def _jd(x):
    return x.decode("utf-8", "replace") if isinstance(x, bytes) else str(x)


def _canon_attr(v) -> bytes:
    a = np.asarray(v)
    if a.dtype.kind in "OU":
        return b"STR" + json.dumps(a.tolist(), default=_jd).encode()
    return (a.dtype.str + repr(a.shape)).encode() + np.ascontiguousarray(a).tobytes()


def attrs_md5(f) -> str:
    objs = []
    f.visititems(lambda name, o: objs.append((name, o)))
    objs.sort(key=lambda t: t[0])
    h = hashlib.md5()
    for name, o in [("/", f)] + objs:
        for k in sorted(o.attrs.keys()):
            if k in TIMESTAMP_ATTRS:
                continue
            h.update(f"{name}\0{k}\0".encode())
            h.update(_canon_attr(o.attrs[k]))
            h.update(b"\0")
    return h.hexdigest()


def file_entry(path) -> dict:
    ds = {}
    with h5py.File(path, "r") as f:
        def visit(name, o):
            if isinstance(o, h5py.Dataset):
                ds[name] = {"dtype": o.dtype.str, "shape": list(o.shape),
                            "md5": dataset_md5(o)}
        f.visititems(visit)
        return {"datasets": dict(sorted(ds.items())), "attrs_md5": attrs_md5(f)}


def list_h5(target):
    target = Path(target)
    return sorted(p for p in target.rglob("*.h5") if p.is_file())


def build_manifest(target, log=print, workers=1) -> dict:
    """workers > 1 hashes the files in that many processes."""
    target = Path(target)
    paths = list_h5(target)
    rels = [str(p.relative_to(target)) for p in paths]
    for rel in rels:
        log(f"manifest {rel}")
    files = dict(zip(rels, pmap(file_entry, paths, workers)))
    return {
        "kit_version": config.KIT_VERSION,
        "lockfile_sha256": config.lockfile_sha256(),
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "files": files,
    }


def write_manifest(target, out, log=print, workers=1) -> dict:
    m = build_manifest(target, log, workers)
    Path(out).write_text(json.dumps(m, indent=1, sort_keys=True) + "\n")
    return m


def load_manifest(path) -> dict:
    return json.loads(Path(path).read_text())


def compare_entries(rel, old, new):
    """List of human-readable mismatch strings (file + h5path) between two file entries."""
    bad = []
    for k in sorted(set(old["datasets"]) | set(new["datasets"])):
        if k not in new["datasets"]:
            bad.append(f"{rel}: dataset {k} missing")
        elif k not in old["datasets"]:
            bad.append(f"{rel}: dataset {k} not in manifest")
        else:
            o, n = old["datasets"][k], new["datasets"][k]
            for fld in ("dtype", "shape", "md5"):
                if o[fld] != n[fld]:
                    bad.append(f"{rel}: dataset {k} {fld} differs ({o[fld]} != {n[fld]})")
    if old["attrs_md5"] != new["attrs_md5"]:
        bad.append(f"{rel}: attributes differ (attrs_md5 {old['attrs_md5']} != {new['attrs_md5']})")
    return bad
