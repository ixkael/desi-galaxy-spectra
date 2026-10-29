"""Verification of a directory of chunk files against its manifest (and, optionally, its shards).

Checks (from the v4_zchunks RUN_SUMMARY): manifest recomputes identically; root n_* equal actual
counts; every clean shard row in exactly one chunk; (targetid, program) unique; z inside
[z_lo, z_hi) and equal to the shard; wavelength identical across files; unit keys present;
spectra_attrs targetid == catalog targetid; has_resolution consistent with the dataset presence
and the dtype equal to RESOLUTION_STORAGE_DTYPE.  Shard-dependent checks need ``shard_dir``.
"""
from __future__ import annotations

import re
from pathlib import Path

import h5py
import numpy as np

from . import config, manifest
from .parallel import pmap
from .package import CLASSES, PRESENT, TABLE_GROUP, VALID_SUFFIX, list_shards

_SHARD_RE = re.compile(r"^healpix_(\d+)\.h5$")

N_UNIT_KEYS = 11            # metadata/units written by the extractor; +1 (resolution) if present


class Report:
    def __init__(self):
        self.checks = []        # (name, ok, [messages])
        self.notes = []         # always printed (explicit opt-outs)

    def add(self, name, msgs, skipped=False):
        self.checks.append((name, not msgs, list(msgs), skipped))

    @property
    def ok(self):
        return all(ok for _, ok, _, _ in self.checks)

    def text(self):
        lines = [f"NOTE: {n}" for n in self.notes]
        for name, ok, msgs, skipped in self.checks:
            tag = "SKIPPED" if skipped else ("PASS" if ok else "FAIL")
            lines.append(f"[{tag}] {name}")
            for m in msgs[:20]:
                lines.append(f"    {m}")
            if len(msgs) > 20:
                lines.append(f"    ... {len(msgs) - 20} more")
        lines.append("VERIFY " + ("PASSED" if self.ok else "FAILED"))
        return "\n".join(lines)


def _chunk_files(target):
    out = []
    for p in manifest.list_h5(target):
        with h5py.File(p, "r") as f:
            if "z_lo" in f.attrs:
                out.append(p)
    return out


def _val(x):
    """Plain comparable form of a table cell or an attribute value."""
    if isinstance(x, bytes):
        return ("s", x.decode("utf-8", "replace"))
    if isinstance(x, str):
        return ("s", x)
    a = np.asarray(x)
    if a.dtype.kind in "OSU":
        return ("sa", a.shape, tuple(v.decode() if isinstance(v, bytes) else str(v)
                                     for v in a.ravel().tolist()))
    return (a.dtype.str, a.shape, np.ascontiguousarray(a).tobytes())


def check_shard_table(f, rel):
    """Messages for the per-shard table of one chunk file (empty list = fine)."""
    msgs = []
    if TABLE_GROUP not in f:
        return [f"{rel}: {TABLE_GROUP}/ missing"]
    t = f[TABLE_GROUP]
    src = [s.decode() if isinstance(s, bytes) else str(s) for s in f.attrs["source_shards"]]
    pix = [int(m.group(1)) for s in src if (m := _SHARD_RE.match(s))]
    if len(pix) != len(src):
        msgs.append(f"{rel}: source_shards has unparsable names")
    if "healpix" not in t:
        return msgs + [f"{rel}: {TABLE_GROUP}/healpix missing"]
    hp = t["healpix"][()].tolist()
    if len(hp) != len(src):
        msgs.append(f"{rel}: table has {len(hp)} rows, source_shards lists {len(src)}")
    if sorted(hp) != sorted(pix) or len(set(hp)) != len(hp):
        msgs.append(f"{rel}: table healpix set != source_shards pixels")
    elif hp != pix:
        msgs.append(f"{rel}: table healpix order != source_shards order")
    n = len(src)

    def check_attrs(label, chunk_attrs, grp, present):
        """chunk-level attrs must be exactly the table columns that are valid and identical
        in all rows where the container is present."""
        cols = [k for k in grp if not k.endswith(VALID_SUFFIX) and k != PRESENT]
        for k in cols:
            ds, valid = grp[k], grp[k + VALID_SUFFIX][()]
            if ds.shape[0] != n or valid.shape != (n,):
                msgs.append(f"{rel}: {label}/{k}: {ds.shape[0]} rows, expected {n}")
                continue
            rows = [i for i in range(n) if present[i]]
            if not rows:
                continue
            if (valid[rows]).all():
                vals = [_val(ds[i]) for i in rows]
                common = all(v == vals[0] for v in vals[1:])
            else:
                common = False
            if common and k not in chunk_attrs:
                msgs.append(f"{rel}: {label}@{k} identical in all shards but missing at chunk level")
            if k in chunk_attrs:
                if not common:
                    msgs.append(f"{rel}: {label}@{k} at chunk level but differs between shards")
                elif _val(chunk_attrs[k]) != _val(ds[rows[0]]):
                    msgs.append(f"{rel}: {label}@{k} chunk value != table value")
        for k in chunk_attrs:
            if k not in cols:
                msgs.append(f"{rel}: {label}@{k} at chunk level but not in table")

    everyone = [True] * n
    if "source_metadata" not in t:
        msgs.append(f"{rel}: {TABLE_GROUP}/source_metadata missing")
    else:
        check_attrs("source_metadata", dict(f["source_metadata"].attrs), t["source_metadata"],
                    everyone)
    for c in CLASSES:
        if c in f and c not in t:
            msgs.append(f"{rel}: class {c} in chunk but not in {TABLE_GROUP}")
        if c in t:
            if PRESENT not in t[c] or t[c][PRESENT].shape != (n,):
                msgs.append(f"{rel}: {TABLE_GROUP}/{c}/{PRESENT} missing or wrong length")
                continue
            present = t[c][PRESENT][()].tolist()
            if c in f:
                check_attrs(c, dict(f[c].attrs), t[c], present)
    return msgs


def verify(target, manifest_path, shard_dir=None, log=print, no_shards=False, workers=1):
    """no_shards=True is the explicit opt-out of the shard-dependent checks; without it, a
    missing shard_dir makes the verification FAIL.  workers > 1 recomputes the manifest
    entries (check 1) in that many processes."""
    if shard_dir is not None and no_shards:
        raise ValueError("shard_dir and no_shards are contradictory")
    target = Path(target)
    rep = Report()
    man = manifest.load_manifest(manifest_path)

    # 1. manifest recomputes identically
    msgs = []
    on_disk = {str(p.relative_to(target)): p for p in manifest.list_h5(target)}
    both = sorted(set(man["files"]) & set(on_disk))
    fresh = dict(zip(both, pmap(manifest.file_entry, [on_disk[r] for r in both], workers)))
    for rel in sorted(set(man["files"]) | set(on_disk)):
        if rel not in on_disk:
            msgs.append(f"{rel}: in manifest but missing on disk")
        elif rel not in man["files"]:
            msgs.append(f"{rel}: on disk but not in manifest")
        else:
            msgs += manifest.compare_entries(rel, man["files"][rel], fresh[rel])
    if man.get("lockfile_sha256") != config.lockfile_sha256():
        msgs.append("lockfile_sha256 in manifest differs from the current lockfile")
    rep.add("manifest recomputes identically", msgs)

    files = _chunk_files(target)
    n_msg, key_msg, z_msg, u_msg, a_msg, r_msg = [], [], [], [], [], []
    t_msg = []
    wave0, wave_msg = None, []
    seen = {}
    key_rows = {}               # (healpix, class, source_row) -> file
    for p in files:
        rel = p.name
        with h5py.File(p, "r") as f:
            a = f.attrs
            lo, hi = float(a["z_lo"]), float(a["z_hi"])
            tot = 0
            has_res = bool(a["has_resolution"]) if "has_resolution" in a else None
            if has_res is None:
                r_msg.append(f"{rel}: root attr has_resolution missing")
            for c in CLASSES:
                n_attr = int(a.get("n_" + c, -1))
                if c not in f:
                    if n_attr != 0:
                        n_msg.append(f"{rel}: n_{c}={n_attr} but group absent")
                    continue
                g = f[c]
                n = len(g["catalog/targetid"])
                tot += n
                if n_attr != n:
                    n_msg.append(f"{rel}: n_{c}={n_attr}, actual {n}")
                for sg in ("catalog", "photometry", "quality"):
                    for k, ds in g[sg].items():
                        if ds.shape[0] != n:
                            n_msg.append(f"{rel}: {c}/{sg}/{k} has {ds.shape[0]} rows, expected {n}")
                if "photsys" not in g["catalog"]:
                    n_msg.append(f"{rel}: {c}/catalog/photsys missing")
                for k in ("flux", "ivar", "mask"):
                    if g["spectra"][k].shape != (n, f["wavelength"].shape[0]):
                        n_msg.append(f"{rel}: {c}/spectra/{k} shape {g['spectra'][k].shape}")
                # key uniqueness
                tid = g["catalog/targetid"][()]
                prog = g["catalog/program"][()]
                for t, pr in zip(tid.tolist(), prog.tolist()):
                    kk = (t, pr)
                    if kk in seen:
                        key_msg.append(f"{rel}: (targetid, program) {kk} also in {seen[kk]}")
                    seen[kk] = rel
                # z range
                z = g["catalog/z_redrock"][()]
                bad = np.nonzero(~((z >= lo) & (z < hi)))[0]
                if len(bad):
                    z_msg.append(f"{rel}: {c} {len(bad)} rows outside [{lo},{hi}) (first row {bad[0]})")
                # spectra_attrs targetid
                if not np.array_equal(g["spectra_attrs/targetid"][()], tid):
                    a_msg.append(f"{rel}: {c} spectra_attrs/targetid != catalog/targetid")
                # resolution presence / dtype
                present = "resolution" in g["spectra"]
                if has_res is not None and present != has_res:
                    r_msg.append(f"{rel}: {c} has_resolution={has_res} but dataset present={present}")
                if present:
                    dt = g["spectra/resolution"].dtype
                    if dt != np.dtype(config.RESOLUTION_STORAGE_DTYPE):
                        r_msg.append(f"{rel}: {c}/spectra/resolution dtype {dt} != "
                                     f"{config.RESOLUTION_STORAGE_DTYPE}")
                hp = g["catalog/healpix"][()]
                sr = g["source_row"][()]
                for h_, r_ in zip(hp.tolist(), sr.tolist()):
                    kk = (h_, c, r_)
                    if kk in key_rows:
                        key_msg.append(f"{rel}: shard row {kk} also in {key_rows[kk]}")
                    key_rows[kk] = rel
            if int(a["n_objects"]) != tot:
                n_msg.append(f"{rel}: n_objects={int(a['n_objects'])}, actual {tot}")
            t_msg += check_shard_table(f, rel)
            # wavelength
            w = f["wavelength"][()]
            if wave0 is None:
                wave0 = w
            elif not np.array_equal(w, wave0):
                wave_msg.append(f"{rel}: wavelength differs from {files[0].name}")
            # units
            nk = N_UNIT_KEYS + (1 if has_res else 0)
            if len(f["units"].attrs) != nk or (has_res and "resolution" not in f["units"].attrs):
                u_msg.append(f"{rel}: {len(f['units'].attrs)} unit keys, expected {nk}")
    if not files:
        n_msg.append(f"no chunk files (root attr z_lo) found in {target}")
    rep.add("root n_* equal actual counts", n_msg)
    rep.add("(targetid, program) unique; no shard row repeated", key_msg)
    rep.add("z inside [z_lo, z_hi)", z_msg)
    rep.add("wavelength identical across files", wave_msg)
    rep.add("unit keys present", u_msg)
    rep.add("spectra_attrs targetid == catalog targetid", a_msg)
    rep.add("has_resolution consistent with datasets; resolution dtype", r_msg)
    rep.add("per-shard table: one row per source shard, healpix = source_shards, "
            "chunk-level attrs = common value", t_msg)

    # shard-dependent checks
    name_sh = "every clean shard row in exactly one chunk; z equals shard"
    if shard_dir is None and no_shards:
        rep.notes.append("--no-shards given: completeness against the shards (no dropped clean "
                         "galaxy, z equals shard) was NOT checked")
        rep.add(name_sh, [], skipped=True)
    elif shard_dir is None:
        rep.add(name_sh, ["not run: pass --shard-dir DIR, or --no-shards to opt out explicitly"])
    else:
        s_msg = []
        expected = {}
        zs = {}
        for pix, sp in list_shards(shard_dir):
            with h5py.File(sp, "r") as f:
                for c in CLASSES:
                    if c not in f:
                        continue
                    cat = f[c]["catalog"]
                    m = cat["is_galaxy"][()].astype(bool) & cat["is_clean_galaxy"][()].astype(bool)
                    z = cat["z_redrock"][()]
                    hp = int(f.attrs["healpix"])
                    for r in np.nonzero(m)[0].tolist():
                        expected[(hp, c, r)] = sp.name
                        zs[(hp, c, r)] = z[r]
        miss = set(expected) - set(key_rows)
        extra = set(key_rows) - set(expected)
        if miss:
            s_msg.append(f"{len(miss)} clean shard rows in no chunk (e.g. {sorted(miss)[:3]})")
        if extra:
            s_msg.append(f"{len(extra)} chunk rows not clean in any shard (e.g. {sorted(extra)[:3]})")
        for p in files:
            with h5py.File(p, "r") as f:
                for c in CLASSES:
                    if c not in f:
                        continue
                    hp = f[c]["catalog/healpix"][()]
                    sr = f[c]["source_row"][()]
                    z = f[c]["catalog/z_redrock"][()]
                    for h_, r_, z_ in zip(hp.tolist(), sr.tolist(), z.tolist()):
                        zz = zs.get((h_, c, r_))
                        if zz is not None and np.float64(zz).tobytes() != np.float64(z_).tobytes():
                            s_msg.append(f"{p.name}: {c} row {(h_, r_)} z differs from shard")
        rep.add("every clean shard row in exactly one chunk; z equals shard", s_msg)
    log(rep.text())
    return rep
