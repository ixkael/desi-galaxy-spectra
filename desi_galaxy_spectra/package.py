"""Cut the clean-galaxy sample of a set of shards into redshift-chunk files (v4_zchunks layout).

The kit's own writer: ``package_desi_zchunks.write_chunk`` assumes one per-object source file,
whereas the shards are already stacked.  Selection, chunk edges, names, datatypes, gzip level and
group layout are those of the packager (imported unmodified).
"""
from __future__ import annotations

import datetime
import hashlib
import os
import re
import time
from pathlib import Path

from . import config

import h5py
import numpy as np

from .frozen import package_desi_zchunks as pk             # frozen packager

CLASSES, NWAVE, NDIAG, OBJ_ATTRS = pk.CLASSES, pk.NWAVE, pk.NDIAG, pk.OBJ_ATTRS
SELECTION, RES_DESC, chunk_list = pk.SELECTION, pk.RES_DESC, pk.chunk_list
SCRIPT_NAME = "dgs package"
BLOCK = 32
GZIP = 4
_SHARD_RE = re.compile(r"^healpix_(\d+)\.h5$")


class PackageError(RuntimeError):
    pass


def list_shards(shard_dir):
    """[(pix, path)] sorted by healpix number."""
    out = []
    for p in Path(shard_dir).iterdir():
        m = _SHARD_RE.match(p.name)
        if m:
            out.append((int(m.group(1)), p))
    return sorted(out)


def md5_file(path, block=1 << 24):
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            b = f.read(block)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def source_manifest_md5(shards):
    """md5 of the md5sum-style listing '<md5>  <name>\\n' of the shards (sorted by pixel)."""
    lines = "".join(f"{md5_file(p)}  {p.name}\n" for _, p in shards)
    return hashlib.md5(lines.encode()).hexdigest()


class ShardSet:
    """Small columns of every shard (clean-galaxy selection per class)."""

    def __init__(self, shards):
        self.shards = shards
        self.f = [h5py.File(p, "r") for _, p in shards]
        self.sel, self.z, self.tid, self.prog = {}, {}, {}, {}
        self._aux = {}
        for i, f in enumerate(self.f):
            for c in CLASSES:
                if c not in f:
                    continue
                cat = f[c]["catalog"]
                m = cat["is_galaxy"][()].astype(bool) & cat["is_clean_galaxy"][()].astype(bool)
                rows = np.nonzero(m)[0]
                self.sel[(i, c)] = rows
                self.z[(i, c)] = cat["z_redrock"][()][rows]
                self.tid[(i, c)] = cat["targetid"][()][rows]
                self.prog[(i, c)] = cat["program"][()][rows]
        self.wave = self.f[0]["wavelength"][()]
        for f, (pix, p) in zip(self.f, shards):
            if not np.array_equal(f["wavelength"][()], self.wave):
                raise PackageError(f"{p.name}: wavelength differs from first shard")
            if "photsys" not in next(f[c]["catalog"] for c in CLASSES if c in f):
                raise PackageError(f"{p.name}: catalog/photsys missing (not a kit shard)")

    def close(self):
        for f in self.f:
            f.close()

    def chunk_rows(self, lo, hi):
        """class -> [(shard_index, source_rows)] in shard order, rows ascending, z in [lo, hi)."""
        out = {}
        for c in CLASSES:
            parts = []
            for i in range(len(self.f)):
                if (i, c) not in self.sel:
                    continue
                m = (self.z[(i, c)] >= lo) & (self.z[(i, c)] < hi)
                if m.any():
                    parts.append((i, self.sel[(i, c)][m]))
            if parts:
                out[c] = parts
        return out

    def aux(self, i, c):
        if (i, c) not in self._aux:
            d = {}
            for sg in ("catalog", "photometry", "quality"):
                d[sg] = {k: (v[()], dict(v.attrs)) for k, v in self.f[i][c][sg].items()}
            self._aux[(i, c)] = d
        return self._aux[(i, c)]


def _concat_aux(ss, c, parts):
    """sg -> key -> (array, attrs), rows concatenated across shards."""
    first = ss.aux(parts[0][0], c)
    res = {}
    for sg, d in first.items():
        res[sg] = {}
        for k, (arr0, at0) in d.items():
            chunks = []
            for i, rows in parts:
                a, at = ss.aux(i, c)[sg][k]
                if a.dtype != arr0.dtype or a.shape[1:] != arr0.shape[1:]:
                    raise PackageError(f"{c}/{sg}/{k}: dtype/shape differs between shards")
                chunks.append(a[rows])
            res[sg][k] = (np.concatenate(chunks), at0)
        for i, _ in parts[1:]:
            if set(ss.aux(i, c)[sg]) != set(d):
                raise PackageError(f"{c}/{sg}: column set differs between shards")
    return res


# ----------------------------------------------------------------- per-shard attribute table
TABLE_GROUP = "source_shard_table"
VALID_SUFFIX = "__valid"
PRESENT = "_present"


def canon_attr(v):
    """Hashable, dtype-faithful form of an HDF5 attribute value (equality = identical value)."""
    if isinstance(v, bytes):
        return ("bytes", bytes(v))
    if isinstance(v, str):
        return ("str", v)
    a = np.asarray(v)
    if a.dtype.kind in "OU":
        return ("strarray", a.shape, tuple(str(x) for x in a.ravel().tolist()))
    return (a.dtype.str, a.shape, np.ascontiguousarray(a).tobytes())


def common_attrs(rows):
    """{key: value} of the attributes that are present and identical in every non-None row
    (the first such row supplies the value, so the original dtype is kept)."""
    present = [r for r in rows if r is not None]
    if not present:
        return {}
    out = {}
    for k, v in present[0].items():
        c = canon_attr(v)
        if all(k in r and canon_attr(r[k]) == c for r in present[1:]):
            out[k] = v
    return out


def _table_dataset(vals, n, key):
    """(array-like data, dtype) of one table column; vals has None where the attribute is absent."""
    first = next(v for v in vals if v is not None)
    if isinstance(first, str) or (np.asarray(first).dtype.kind in "OU"):
        shape = () if isinstance(first, str) else np.asarray(first).shape
        data = np.empty((n,) + shape, dtype=object)
        for i, v in enumerate(vals):
            if v is None:
                data[i] = "" if not shape else np.full(shape, "", dtype=object)
            elif isinstance(v, str) or shape == ():
                data[i] = str(v)
            else:
                data[i] = np.asarray(v).astype(object)
        return data, h5py.string_dtype()
    arrs = [None if v is None else np.asarray(v) for v in vals]
    ref = next(a for a in arrs if a is not None)
    for a in arrs:
        if a is not None and (a.dtype != ref.dtype or a.shape != ref.shape):
            raise PackageError(f"per-shard table: attribute {key} has differing dtype/shape "
                               f"between shards ({a.dtype}{a.shape} vs {ref.dtype}{ref.shape})")
    if ref.dtype.kind == "f":
        fill = np.full(ref.shape, np.nan, dtype=ref.dtype)
    else:
        fill = np.zeros(ref.shape, dtype=ref.dtype)        # b -> False, S -> b"", ints -> 0
    data = np.stack([fill if a is None else a for a in arrs])
    return data, ref.dtype


def write_table_group(grp, rows):
    """One dataset per attribute (row i = shard i) plus a bool `<attr>__valid` dataset that is
    False where the shard lacks the group or the attribute (the value there is a fill: NaN for
    floats, 0 / False / empty string otherwise, and carries no information)."""
    n = len(rows)
    keys = []
    for r in rows:
        for k in (r or {}):
            if k not in keys:
                keys.append(k)
    for k in keys:
        vals = [None if r is None or k not in r else r[k] for r in rows]
        data, dt = _table_dataset(vals, n, k)
        grp.create_dataset(k, data=data, dtype=dt)
        grp.create_dataset(k + VALID_SUFFIX, data=np.array([v is not None for v in vals], bool))


def write_shard_table(out, ss):
    """`source_shard_table/`: healpix (i8), source_metadata/<attr>, <CLASS>/<attr>, <CLASS>/_present."""
    t = out.create_group(TABLE_GROUP)
    t.create_dataset("healpix", data=np.array([int(f.attrs["healpix"]) for f in ss.f], "i8"))
    write_table_group(t.create_group("source_metadata"),
                      [dict(f["metadata"].attrs) for f in ss.f])
    for c in CLASSES:
        rows = [dict(f[c].attrs) if c in f else None for f in ss.f]
        if all(r is None for r in rows):
            continue
        gc = t.create_group(c)
        gc.create_dataset(PRESENT, data=np.array([r is not None for r in rows], bool))
        write_table_group(gc, rows)


def write_chunk(ss, idx, name, lo, hi, out_dir, with_resolution, manifest_md5, log=print):
    final = os.path.join(out_dir, name)
    part = final + ".partial"
    shard_names = [p.name for _, p in ss.shards]
    cr = ss.chunk_rows(lo, hi)
    if not cr:
        log(f"[{idx}] {name}: no objects, skipping")
        return None
    if os.path.exists(final):
        with h5py.File(final, "r") as f:
            if f.attrs.get("source_manifest_md5") != manifest_md5:
                raise PackageError(f"{final} exists from a different shard set; remove it")
            if TABLE_GROUP not in f:
                raise PackageError(f"{final} has no {TABLE_GROUP}/ (written by kit < 0.2.0); "
                                   "remove it")
            if bool(f.attrs.get("has_resolution")) != with_resolution:
                raise PackageError(f"{final} exists with a different resolution setting")
        log(f"[{idx}] {name} exists, skipping")
        return final
    counts = {c: sum(len(r) for _, r in parts) for c, parts in cr.items()}
    ntot = sum(counts.values())
    log(f"[{idx}] {name}: {ntot} objects ({', '.join(f'{c}={n}' for c, n in counts.items())})")
    res_dtype = np.dtype(config.RESOLUTION_STORAGE_DTYPE)
    first = ss.f[0]
    with h5py.File(part, "w") as out:
        out.create_dataset("wavelength", data=ss.wave)
        out["wavelength"].attrs["units"] = "Angstrom (vacuum, solar-system barycentric)"
        # source_metadata keeps only values identical in every source shard; the per-shard values
        # (all of them) live in source_shard_table/
        sm = out.create_group("source_metadata")
        for k, v in common_attrs([dict(f["metadata"].attrs) for f in ss.f]).items():
            sm.attrs[k] = v
        un = out.create_group("units")
        for k, v in first["metadata/units"].attrs.items():
            un.attrs[k] = v
        if with_resolution:
            un.attrs["resolution"] = RES_DESC
        a = out.attrs
        a["selection"] = SELECTION
        a["z_lo"] = float(lo)
        a["z_hi"] = float(hi)
        a["redshift_column"] = "z_redrock"
        a["bin_convention"] = "[z_lo, z_hi)"
        a["source_shards"] = np.array(shard_names, dtype="S")
        a["source_manifest_md5"] = manifest_md5
        a["has_resolution"] = bool(with_resolution)
        a["created"] = datetime.datetime.now().isoformat(timespec="seconds")
        a["script"] = SCRIPT_NAME
        a["n_objects"] = ntot
        for c in CLASSES:
            a["n_" + c] = counts.get(c, 0)

        for c, parts in cr.items():
            n = counts[c]
            g = out.create_group(c)
            # group attrs: only values identical in every source shard that has this class
            for k, v in common_attrs([dict(f[c].attrs) if c in f else None
                                      for f in ss.f]).items():
                g.attrs[k] = v
            for sg, d in _concat_aux(ss, c, parts).items():
                gg = g.create_group(sg)
                for k, (arr, at) in d.items():
                    ds = gg.create_dataset(k, data=arr)
                    for ak, av in at.items():
                        ds.attrs[ak] = av
            g.create_dataset("source_row", data=np.concatenate([r for _, r in parts]).astype("i8"))
            sp = g.create_group("spectra")
            kw = dict(compression="gzip", compression_opts=GZIP, shuffle=False)
            d = {
                "flux": sp.create_dataset("flux", (n, NWAVE), "f4", chunks=(1, NWAVE), **kw),
                "ivar": sp.create_dataset("ivar", (n, NWAVE), "f4", chunks=(1, NWAVE), **kw),
                "mask": sp.create_dataset("mask", (n, NWAVE), "i2", chunks=(1, NWAVE), **kw),
            }
            if with_resolution:
                d["resolution"] = sp.create_dataset(
                    "resolution", (n, NDIAG, NWAVE), res_dtype,
                    chunks=(1, NDIAG, NWAVE), **kw)
            sa = g.create_group("spectra_attrs")
            for k, dt in OBJ_ATTRS.items():
                d["attr_" + k] = sa.create_dataset(k, (n,), dt)
            tid_cat = g["catalog/targetid"][()]
            pos = 0
            for i, rows in parts:
                src = ss.f[i][c]
                for s in range(0, len(rows), BLOCK):
                    rb = rows[s:s + BLOCK]
                    m = len(rb)
                    flux = np.empty((m, NWAVE), "f4")
                    ivar = np.empty((m, NWAVE), "f4")
                    mask = np.empty((m, NWAVE), "i2")
                    res = np.empty((m, NDIAG, NWAVE), "f4") if with_resolution else None
                    for j, r in enumerate(rb):
                        r = int(r)
                        src["spectra/flux"].read_direct(flux, np.s_[r], np.s_[j])
                        src["spectra/ivar"].read_direct(ivar, np.s_[r], np.s_[j])
                        src["spectra/mask"].read_direct(mask, np.s_[r], np.s_[j])
                        if with_resolution:
                            src["spectra/resolution"].read_direct(res, np.s_[r, :, :], np.s_[j, :, :])
                    attrs = {k: src["spectra_attrs"][k][()][rb] for k in OBJ_ATTRS}
                    if not np.array_equal(attrs["targetid"], tid_cat[pos:pos + m]):
                        raise PackageError(f"{c}: spectra_attrs targetid != catalog targetid "
                                           f"(shard {ss.shards[i][1].name}, rows {rb[:3]}...)")
                    e = pos + m
                    d["flux"][pos:e] = flux
                    d["ivar"][pos:e] = ivar
                    d["mask"][pos:e] = mask
                    if with_resolution:
                        d["resolution"][pos:e] = res.astype(res_dtype, copy=False)
                    for k in OBJ_ATTRS:
                        d["attr_" + k][pos:e] = attrs[k]
                    pos = e
            assert pos == n
        write_shard_table(out, ss)
    os.replace(part, final)
    log(f"[{idx}] wrote {final} ({os.path.getsize(final) / 1e6:.1f} MB)")
    return final


def dry_run(ss, chunks, with_resolution, log=print):
    bpo = pk.BYTES_PER_OBJ if with_resolution else pk.BYTES_PER_OBJ - NDIAG * NWAVE * 4
    log("%3s  %-46s %-14s %7s %7s %7s %8s %10s" % (
        "idx", "file", "z range", "n_BGS", "n_ELG", "n_LRG", "total", "MB_raw"))
    tot = {c: 0 for c in CLASSES}
    for i, name, lo, hi in chunks:
        cr = ss.chunk_rows(lo, hi)
        n = {c: sum(len(r) for _, r in cr.get(c, [])) for c in CLASSES}
        for c in CLASSES:
            tot[c] += n[c]
        t = sum(n.values())
        log("%3d  %-46s %-14s %7d %7d %7d %8d %10.1f" % (
            i, name, "[%g,%g)" % (lo, hi), n["BGS"], n["ELG"], n["LRG"], t, t * bpo / 1e6))
    return tot


def package(shard_dir, out_dir, with_resolution=True, only=None, dry=False, log=print):
    shards = list_shards(shard_dir)
    if not shards:
        raise PackageError(f"no healpix_<pix>.h5 shards in {shard_dir}")
    chunks = chunk_list()
    if only:
        bad = [w for w in only if not 0 <= w < len(chunks)]
        if bad:
            raise PackageError(f"bad chunk indices {bad} (valid 0..{len(chunks) - 1})")
        chunks = [c for c in chunks if c[0] in only]
    t0 = time.time()
    ss = ShardSet(shards)
    try:
        if dry:
            return dry_run(ss, chunks, with_resolution, log)
        os.makedirs(out_dir, exist_ok=True)
        md5 = source_manifest_md5(shards)
        written = []
        for idx, name, lo, hi in chunks:
            f = write_chunk(ss, idx, name, lo, hi, out_dir, with_resolution, md5, log)
            if f:
                written.append(f)
        log(f"done in {(time.time() - t0) / 60:.1f} min")
        return written
    finally:
        ss.close()
