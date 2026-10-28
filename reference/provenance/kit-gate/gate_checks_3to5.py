#!/usr/bin/env python
"""Gate checks 3, 4 and 5 of the kit gate (pixel 21852).  Checks 1-2 are written
independently by the verifier and are not here.

  check 3  original scripts/package_desi_zchunks.py on the reference  vs  kit packager on the shard:
           identical except declared provenance attrs and the approved additions (every
           excluded item is listed in the JSON with the values actually observed)
  check 4  kit --no-resolution output == check-3 kit output minus resolution (has_resolution False)
  check 5  manifest round trip; a 1-ULP change injected into one flux value of a copied chunk is
           detected, and named, by `dgs verify`

Comparisons of 3 and 4 use h5py/numpy only (no kit code).  Check 5 uses the kit manifest/verify
by construction.

Fix session 2026-10-23 (N1): a comparison now FAILS if the file sets are empty or differ, or if
n_files_compared is not the number of non-empty chunk files.  The kit-0.2.0 per-shard table
(/source_shard_table) is an approved addition in check 3: excluded from the A-vs-B comparison, but
its contents are asserted (one row, healpix 21852, every value equal to the shard attribute).

Usage: gate_checks_3to5.py --gate-dir DIR [--out FILE] [--scratch DIR]
--out defaults to <gate-dir>/gate_checks_3to5.json.  --gate-dir has no default.  --scratch
defaults to a fresh temporary directory.

Ported to the public repository: the kit's `desikit.manifest` is `desi_galaxy_spectra.manifest`
and the kit's `kit.py verify` is the `dgs verify` command (`python -m desi_galaxy_spectra.cli`).
If a gate run needs the extract step, `dgs extract --local-flat DIR --redrock-dir DIR` must now
be given `--staging` and `--workers` explicitly (gate mode has no defaults); this script does
not run it.
Gate dir layout: chunks_original/, chunks_kit_res/, chunks_kit_nores/, manifest.json,
healpix_21852.h5.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import h5py
import numpy as np

KIT = Path(__file__).resolve().parents[3]       # the repository root
CLASSES = ("BGS", "ELG", "LRG")
BLOCK_BYTES = 32 * 1024 * 1024
TABLE_REASON = "approved addition (PI 2026-10-23, B2): per-shard metadata table; contents asserted"
PIX = 21852


def canon(v):
    a = np.asarray(v)
    if a.dtype.kind in "OU":
        return ("str", json.dumps(a.tolist(), default=lambda x: x.decode() if isinstance(x, bytes) else str(x)))
    return (a.dtype.str, tuple(a.shape), np.ascontiguousarray(a).tobytes())


def show(v):
    a = np.asarray(v)
    if a.dtype.kind == "S":
        v = [x.decode() for x in a.ravel()] if a.ndim else a.item().decode()
    elif a.dtype.kind == "O":
        v = a.tolist()
    else:
        v = a.tolist()
    s = json.dumps(v, default=str)
    return s if len(s) < 300 else s[:300] + "..."


def blocks(ds):
    if ds.shape == () or ds.shape[0] == 0:
        yield ds[()]
        return
    rb = max(1, int(np.prod(ds.shape[1:], dtype=np.int64)) * ds.dtype.itemsize)
    step = max(1, BLOCK_BYTES // rb)
    for s in range(0, ds.shape[0], step):
        yield ds[s:s + step]


def collect(f):
    objs = {"/": f}
    f.visititems(lambda n, o: objs.__setitem__("/" + n, o))
    return objs


def compare_files(fa, fb, a_only_ok=(), b_only_ok=(), attr_skip=None, attr_a_only=(),
                  attr_b_only=(), dset_value_skip=None):
    """Byte-level comparison.  Returns (diffs, excluded, stats).

    a_only_ok / b_only_ok : object paths allowed to exist in only one file (recorded)
    attr_skip             : {(objpath, attr): reason}  attribute present in both, value ignored
    attr_a_only/b_only    : {(objpath, attr): reason}  attribute allowed in only one file
    dset_value_skip       : {objpath: reason}  dataset present in both; basenames compared only
    """
    attr_skip = attr_skip or {}
    dset_value_skip = dset_value_skip or {}
    diffs, excluded = [], []
    stats = {"datasets_compared_bytes_equal": 0, "bytes_compared": 0, "attrs_compared": 0,
             "objects_compared": 0}
    with h5py.File(fa, "r") as A, h5py.File(fb, "r") as B:
        oa, ob = collect(A), collect(B)
        for p in sorted(set(oa) - set(ob)):
            if p in a_only_ok:
                excluded.append({"item": f"object {p}", "kind": "only in A", "reason": a_only_ok[p]})
            else:
                diffs.append(f"object {p} only in A")
        for p in sorted(set(ob) - set(oa)):
            if p in b_only_ok:
                excluded.append({"item": f"object {p}", "kind": "only in B", "reason": b_only_ok[p]})
            else:
                diffs.append(f"object {p} only in B")
        for p in sorted(set(oa) & set(ob)):
            x, y = oa[p], ob[p]
            stats["objects_compared"] += 1
            if isinstance(x, h5py.Dataset) != isinstance(y, h5py.Dataset):
                diffs.append(f"{p}: group vs dataset")
                continue
            # attributes
            ka, kb = set(x.attrs), set(y.attrs)
            for k in sorted(ka - kb):
                if (p, k) in attr_a_only:
                    excluded.append({"item": f"attr {p}@{k}", "kind": "only in A",
                                     "reason": attr_a_only[(p, k)], "a": show(x.attrs[k])})
                else:
                    diffs.append(f"attr {p}@{k} only in A")
            for k in sorted(kb - ka):
                if (p, k) in attr_b_only:
                    excluded.append({"item": f"attr {p}@{k}", "kind": "only in B",
                                     "reason": attr_b_only[(p, k)], "b": show(y.attrs[k])})
                else:
                    diffs.append(f"attr {p}@{k} only in B")
            for k in sorted(ka & kb):
                if (p, k) in attr_skip:
                    excluded.append({"item": f"attr {p}@{k}", "kind": "value ignored",
                                     "reason": attr_skip[(p, k)], "a": show(x.attrs[k]),
                                     "b": show(y.attrs[k])})
                    continue
                stats["attrs_compared"] += 1
                if canon(x.attrs[k]) != canon(y.attrs[k]):
                    diffs.append(f"attr {p}@{k} differs: {show(x.attrs[k])} != {show(y.attrs[k])}")
            if not isinstance(x, h5py.Dataset):
                continue
            # storage
            for fld in ("dtype", "shape", "compression", "compression_opts", "chunks", "shuffle"):
                if getattr(x, fld) != getattr(y, fld):
                    diffs.append(f"{p}: {fld} {getattr(x, fld)} != {getattr(y, fld)}")
            if x.shape != y.shape or x.dtype != y.dtype:
                continue
            if p in dset_value_skip:
                a, b = x[()], y[()]
                same_base = [os.path.basename(s.decode()) for s in a] == \
                            [os.path.basename(s.decode()) for s in b]
                diffs += [] if same_base else [f"{p}: basenames differ"]
                excluded.append({"item": f"dataset {p}", "kind": "directory part ignored",
                                 "reason": dset_value_skip[p], "a_first": show(a[:1]),
                                 "b_first": show(b[:1]), "basenames_equal": same_base})
                continue
            ok = True
            for ba, bb in zip(blocks(x), blocks(y)):
                ba, bb = np.ascontiguousarray(ba), np.ascontiguousarray(bb)
                stats["bytes_compared"] += ba.nbytes
                # variable-length strings are object arrays: compare values, not pointers
                same = (ba.tolist() == bb.tolist()) if ba.dtype.kind == "O" \
                    else (ba.tobytes() == bb.tobytes())
                if not same:
                    ok = False
                    break
            if ok:
                stats["datasets_compared_bytes_equal"] += 1
            else:
                diffs.append(f"{p}: data bytes differ")
    return diffs, excluded, stats


def nonempty_chunks(d):
    """Names of the chunk files in `d` holding at least one object in some class group."""
    names = []
    for p in sorted(Path(d).glob("*.h5")):
        with h5py.File(p, "r") as f:
            if any(c in f and f[c]["catalog/targetid"].shape[0] > 0 for c in CLASSES):
                names.append(p.name)
    return names


def compare_dirs(da, db, kwf):
    fa = {p.name: p for p in Path(da).glob("*.h5")}
    fb = {p.name: p for p in Path(db).glob("*.h5")}
    res = {"files_only_in_A": sorted(set(fa) - set(fb)), "files_only_in_B": sorted(set(fb) - set(fa)),
           "files": {}}
    ok = not res["files_only_in_A"] and not res["files_only_in_B"]
    # N1: an empty or mismatched file set must never pass
    expected = nonempty_chunks(da)
    res["n_nonempty_chunks_A"] = len(expected)
    res["n_nonempty_chunks_B"] = len(nonempty_chunks(db))
    res["file_set_problems"] = []
    if not fa or not fb:
        res["file_set_problems"].append("no .h5 files in one or both directories")
    if set(fa) != set(fb):
        res["file_set_problems"].append("file name sets differ")
    if len(expected) != res["n_nonempty_chunks_B"] or set(expected) != set(nonempty_chunks(db)):
        res["file_set_problems"].append("non-empty chunk sets differ between A and B")
    ok &= not res["file_set_problems"]
    for name in sorted(set(fa) & set(fb)):
        kw2 = kwf(name)
        diffs, excl, stats = compare_files(fa[name], fb[name], **kw2)
        res["files"][name] = {"identical_modulo_declared": not diffs, "diffs": diffs[:50],
                              "n_diffs": len(diffs), "excluded": excl, "stats": stats}
        ok &= not diffs
    res["n_files_compared"] = len(res["files"])
    if res["n_files_compared"] == 0 or res["n_files_compared"] != len(expected):
        res["file_set_problems"].append(
            f"n_files_compared={res['n_files_compared']} != non-empty chunks={len(expected)} (or 0)")
        ok = False
    res["pass"] = bool(ok)
    return res


def classes_in(path):
    with h5py.File(path, "r") as f:
        return [c for c in CLASSES if c in f]


def _txt(v):
    return v.decode() if isinstance(v, bytes) else v


def assert_shard_table(f, shard_path, name):
    """Contents of /source_shard_table in chunk file `f`: one row, healpix PIX, every value equal
    to the corresponding attribute of the shard.  Returns a list of failure strings."""
    bad = []
    if "source_shard_table" not in f:
        return [f"{name}: source_shard_table missing"]
    t = f["source_shard_table"]
    hp = t["healpix"][()] if "healpix" in t else None
    if hp is None or hp.shape != (1,) or int(hp[0]) != PIX:
        bad.append(f"{name}: table healpix {None if hp is None else hp.tolist()} != [{PIX}]")

    def same(col, valid, attr, where):
        if col.shape[0] != 1:
            bad.append(f"{name}: {where} has {col.shape[0]} rows, expected 1")
            return
        if attr is None:
            if bool(valid[0]):
                bad.append(f"{name}: {where} marked valid but the shard has no such attribute")
            return
        if not bool(valid[0]):
            bad.append(f"{name}: {where} marked invalid but the shard has it")
            return
        if canon(_txt(col[0])) != canon(_txt(attr)):
            bad.append(f"{name}: {where} {show(col[0])} != shard {show(attr)}")

    def table_attrs(grp, shard_attrs, label):
        names = {k[:-len("__valid")] for k in grp if k.endswith("__valid")}
        for k in sorted(set(shard_attrs) | names):
            if k not in grp or k + "__valid" not in grp:
                bad.append(f"{name}: {label}/{k} missing from table")
                continue
            same(grp[k][()], grp[k + "__valid"][()], shard_attrs.get(k), f"{label}/{k}")

    with h5py.File(shard_path, "r") as s:
        if "source_metadata" not in t:
            bad.append(f"{name}: table has no source_metadata")
        else:
            table_attrs(t["source_metadata"], dict(s["metadata"].attrs), "source_metadata")
        for c in CLASSES:
            in_shard = c in s
            if in_shard != (c in t):
                bad.append(f"{name}: class {c} in shard={in_shard} but in table={c in t}")
                continue
            if not in_shard:
                continue
            pr = t[c]["_present"][()] if "_present" in t[c] else None
            if pr is None or pr.tolist() != [True]:
                bad.append(f"{name}: {c}/_present != [True]")
            table_attrs(t[c], dict(s[c].attrs), c)
    return bad


def check3(gate):
    """original packager on the reference (A) vs kit packager on the shard (B)."""
    def kw(name):
        cls = classes_in(gate / "chunks_kit_res" / name)
        root = "/"
        table = {}
        with h5py.File(gate / "chunks_kit_res" / name, "r") as f:
            if "source_shard_table" in f:
                table["/source_shard_table"] = TABLE_REASON
                f["source_shard_table"].visit(
                    lambda n: table.__setitem__("/source_shard_table/" + n, TABLE_REASON))
        return dict(
            b_only_ok={**{f"/{c}/catalog/photsys": "approved addition: PHOTSYS recorded (S1)"
                          for c in cls}, **table},
            attr_skip={
                ("/", "created"): "declared provenance (timestamp)",
                ("/", "script"): "declared provenance (writer name)",
                ("/source_metadata", "creation_date"):
                    "declared provenance (timestamp of the extraction that produced the source)",
            },
            attr_a_only={
                (root, "source_file"): "approved change: source_file -> source_shards",
                (root, "source_md5"): "approved change: source_md5 -> source_manifest_md5",
            },
            attr_b_only={
                (root, "source_shards"): "approved addition: list of shard names",
                (root, "source_manifest_md5"): "approved addition: md5 of the shard listing",
                (root, "has_resolution"): "approved addition: True/False, always written",
            },
            dset_value_skip={
                f"/{c}/catalog/coadd_file": "directory part of the path (reference extraction ran "
                                            "elsewhere), as in step 3"
                for c in cls},
        )
    ref = gate / "chunks_original"
    res = compare_dirs(ref, gate / "chunks_kit_res", kw)
    # approved additions: assert the observed values
    extra = []
    shard_names = {"healpix_21852.h5"}
    for name in res["files"]:
        with h5py.File(gate / "chunks_kit_res" / name, "r") as f:
            if bool(f.attrs["has_resolution"]) is not True:
                extra.append(f"{name}: has_resolution != True")
            if {s.decode() for s in f.attrs["source_shards"]} != shard_names:
                extra.append(f"{name}: source_shards != {sorted(shard_names)}")
            for c in classes_in(gate / "chunks_kit_res" / name):
                ps = f[c]["catalog/photsys"][()]
                if ps.dtype != np.dtype("S1") or ps.shape[0] != f[c]["catalog/targetid"].shape[0]:
                    extra.append(f"{name}/{c}: photsys dtype/shape {ps.dtype} {ps.shape}")
            extra += assert_shard_table(f, gate / f"healpix_{PIX}.h5", name)
    res["additions_assertions_failed"] = extra
    res["pass"] = bool(res["pass"] and not extra)
    res["declared_exclusions"] = [
        "root attrs: created, script (value ignored)", "source_metadata@creation_date (value ignored)",
        "root attr source_file, source_md5 (only in original)",
        "root attr source_shards, source_manifest_md5, has_resolution (only in kit)",
        "dataset <class>/catalog/photsys (only in kit)",
        "group /source_shard_table and everything under it (only in kit; contents asserted)",
        "dataset <class>/catalog/coadd_file: directory part ignored, basenames compared"]
    return res


def check4(gate):
    """kit --no-resolution (B) vs kit with resolution (A): identical minus resolution."""
    def kw(name):
        cls = classes_in(gate / "chunks_kit_res" / name)
        return dict(
            a_only_ok={f"/{c}/spectra/resolution": "dataset removed by --no-resolution" for c in cls},
            attr_a_only={("/units", "resolution"): "unit description of the absent dataset"},
            attr_skip={("/", "created"): "declared provenance (timestamp)",
                       ("/", "has_resolution"): "True vs False by design"},
        )
    res = compare_dirs(gate / "chunks_kit_res", gate / "chunks_kit_nores", kw)
    extra = []
    for name in res["files"]:
        with h5py.File(gate / "chunks_kit_nores" / name, "r") as f:
            if bool(f.attrs["has_resolution"]) is not False:
                extra.append(f"{name}: has_resolution != False")
            for c in classes_in(gate / "chunks_kit_nores" / name):
                if "resolution" in f[c]["spectra"]:
                    extra.append(f"{name}/{c}: resolution present")
        with h5py.File(gate / "chunks_kit_res" / name, "r") as f:
            if bool(f.attrs["has_resolution"]) is not True:
                extra.append(f"{name} (res): has_resolution != True")
    res["has_resolution_assertions_failed"] = extra
    res["pass"] = bool(res["pass"] and not extra)
    return res


def run_kit(args):
    p = subprocess.run([sys.executable, "-m", "desi_galaxy_spectra.cli"] + [str(a) for a in args],
                       capture_output=True, text=True, cwd=KIT,
                       env={**os.environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                            "MKL_NUM_THREADS": "1", "NUMBA_NUM_THREADS": "1"})
    return p.returncode, p.stdout + p.stderr


def check5(gate, scratch):
    sys.path.insert(0, str(KIT))
    from desi_galaxy_spectra import manifest
    out = {}
    tgt, man_path = gate / "chunks_kit_res", gate / "manifest.json"
    stored = json.loads(man_path.read_text())
    again = manifest.build_manifest(tgt, log=lambda m: None)
    out["manifest_roundtrip_identical"] = (stored["files"] == again["files"]
                                           and stored["lockfile_sha256"] == again["lockfile_sha256"]
                                           and stored["kit_version"] == again["kit_version"])
    out["manifest_n_files"] = len(stored["files"])
    rc, txt = run_kit(["verify", "--target", tgt, "--manifest", man_path,
                       "--shard-dir", gate])
    out["verify_pristine"] = {"returncode": rc, "pass": rc == 0, "output": txt.splitlines()}
    # 1-ULP injection into a copy
    work = Path(scratch) / "ulp_copy"
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(tgt, work)
    victim = None
    for p in sorted(work.glob("*.h5")):
        with h5py.File(p, "r") as f:
            for c in CLASSES:
                if c in f and f[c]["spectra/flux"].shape[0] > 0:
                    victim = (p, c)
                    break
        if victim:
            break
    p, c = victim
    with h5py.File(p, "r+") as f:
        d = f[c]["spectra/flux"]
        row = d[0]
        j = int(np.argmax(np.isfinite(row) & (row != 0)))
        old = float(row[j])
        row[j] = np.nextafter(row[j], np.float32(np.inf))
        d[0] = row
        new = float(row[j])
    rc, txt = run_kit(["verify", "--target", work, "--manifest", man_path, "--shard-dir", gate])
    named = (p.name in txt) and (f"{c}/spectra/flux" in txt)
    out["ulp_injection"] = {"file": p.name, "dataset": f"{c}/spectra/flux", "row": 0, "col": j,
                            "old": old, "new": new,
                            "ulp_changed": bool(np.float32(old) != np.float32(new)),
                            "verify_returncode": rc, "verify_failed_as_required": rc != 0,
                            "failure_names_file_and_dataset": named,
                            "output": txt.splitlines()[:40]}
    shutil.rmtree(work)
    out["pass"] = bool(out["manifest_roundtrip_identical"] and out["verify_pristine"]["pass"]
                       and out["ulp_injection"]["verify_failed_as_required"]
                       and out["ulp_injection"]["failure_names_file_and_dataset"]
                       and out["ulp_injection"]["ulp_changed"])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--gate-dir", required=True)
    ap.add_argument("--out", default=None,
                    help="result JSON (default: <gate-dir>/gate_checks_3to5.json)")
    ap.add_argument("--scratch", default=None,
                    help="working directory for the 1-ULP copy (default: a new temporary directory)")
    a = ap.parse_args()
    if a.scratch is None:
        a.scratch = tempfile.mkdtemp(prefix="gate-checks-")
    gate = Path(a.gate_dir)
    t0 = time.time()
    res = {"gate_dir": str(gate), "kit_dir": str(KIT)}
    res["check3"] = check3(gate)
    print("check 3:", "PASS" if res["check3"]["pass"] else "FAIL", flush=True)
    res["check4"] = check4(gate)
    print("check 4:", "PASS" if res["check4"]["pass"] else "FAIL", flush=True)
    res["check5"] = check5(gate, a.scratch)
    print("check 5:", "PASS" if res["check5"]["pass"] else "FAIL", flush=True)
    res["all_pass"] = all(res[k]["pass"] for k in ("check3", "check4", "check5"))
    res["seconds"] = time.time() - t0
    out = Path(a.out) if a.out else gate / "gate_checks_3to5.json"
    out.write_text(json.dumps(res, indent=1, default=str) + "\n")
    return 0 if res["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
