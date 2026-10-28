#!/usr/bin/env python
"""Cut the clean-galaxy sample of the DESI DR1 v4 extraction file into
redshift-chunk HDF5 packages for collaborators.

Selection: catalog/is_galaxy & catalog/is_clean_galaxy (in BGS, ELG, LRG).
Redshift column: catalog/z_redrock.  15 half-open chunks [z_lo, z_hi):
  (-inf,0.1), [0.1,0.2) ... [1.1,1.2), [1.2,1.4), [1.4,1.6), [1.6,inf).

Each output file holds one group per class present (catalog, photometry,
quality subset to the chosen rows; `source_row` index into the source class
group; stacked `spectra` arrays; per-object attributes as 1-D datasets in
`spectra_attrs`), a single shared `wavelength` dataset, and provenance
(`source_metadata`, `units`, root attributes).  Every packaged object's
wavelength is asserted identical to the shared one, and every object's
targetid attribute is asserted equal to the catalog targetid.

Spectra are read with a multiprocessing pool (each worker opens the source
read-only once), because per-object reads on a network share are latency
bound.  Files are written as <name>.h5.partial and renamed on success, so a
run is resumable (existing final files are skipped unless --overwrite).

Examples
  package_desi_zchunks.py --v4 FILE --out DIR --dry-run --time-sample 50
  package_desi_zchunks.py --v4 FILE --out DIR --only 0,14
"""
import argparse
import datetime
import multiprocessing as mp
import os
import sys
import threading
import time

import h5py
import numpy as np

CLASSES = ["BGS", "ELG", "LRG"]
SELECTION = "is_galaxy & is_clean_galaxy"
NWAVE = 7781
NDIAG = 11
# per-object uncompressed bytes: flux+ivar f4, mask i2, resolution f4
BYTES_PER_OBJ = 2 * NWAVE * 4 + NWAVE * 2 + NDIAG * NWAVE * 4
INF = float("inf")
EDGES = ([-INF] + [round(0.1 * k, 1) for k in range(1, 13)]
         + [1.4, 1.6, INF])
DEFAULT_MD5 = "05b36b97f84f6e086eb82d59017501f1"
RES_DESC = ("DESI resolution-matrix diagonals as stored in the DR1 coadd "
            "(11, n_wave), dimensionless")

# per-object attribute name -> output dtype
OBJ_ATTRS = {
    "arm_B_coverage": "f8", "arm_R_coverage": "f8", "arm_Z_coverage": "f8",
    "extraction_success": "bool", "n_pixels": "i8", "resolution_n_diag": "i8",
    "snr_median": "f8", "targetid": "i8",
}


# ----------------------------------------------------------------- chunks
def chunk_list():
    """List of (index, name, z_lo, z_hi)."""
    out = []
    for i in range(len(EDGES) - 1):
        lo, hi = EDGES[i], EDGES[i + 1]
        if lo == -INF:
            tag = "zlt%.2f" % hi
        elif hi == INF:
            tag = "z%.2f-inf" % lo
        else:
            tag = "z%.2f-%.2f" % (lo, hi)
        out.append((i, "desi_dr1_clean_galaxies_%s.h5" % tag, lo, hi))
    return out


# ---------------------------------------------------------------- workers
_W = {}


def _worker_init(path):
    """Open the source file once per worker and keep the handle."""
    _W["f"] = h5py.File(path, "r")


def _read_block(task):
    """Read one block of objects of one class.  Returns stacked arrays."""
    cls, rows, wave_ref = task
    spectra = _W["f"][cls]["spectra"]
    n = len(rows)
    flux = np.empty((n, NWAVE), "f4")
    ivar = np.empty((n, NWAVE), "f4")
    mask = np.empty((n, NWAVE), "i2")
    res = np.empty((n, NDIAG, NWAVE), "f4")
    attrs = {k: np.empty(n, dtype=d) for k, d in OBJ_ATTRS.items()}
    wave_ok = True
    for j, r in enumerate(rows):
        g = spectra["obj_%05d" % r]
        g["flux"].read_direct(flux, np.s_[:], np.s_[j])
        g["ivar"].read_direct(ivar, np.s_[:], np.s_[j])
        g["mask"].read_direct(mask, np.s_[:], np.s_[j])
        g["resolution"].read_direct(res, np.s_[:, :], np.s_[j, :, :])
        w = g["wavelength"][()]
        if wave_ref is not None and not np.array_equal(w, wave_ref):
            wave_ok = False
        for k in OBJ_ATTRS:
            attrs[k][j] = g.attrs[k]
    return cls, flux, ivar, mask, res, attrs, wave_ok


# ------------------------------------------------------------- selection
class Source:
    """Main-process view of the source catalogs (small columns only)."""

    def __init__(self, path):
        self.path = path
        self.f = h5py.File(path, "r")
        self.sel = {}      # class -> source row indices (clean galaxies)
        self.z = {}        # class -> z_redrock of the selected rows
        self.tid = {}
        self.prog = {}
        self.aux = {}      # class -> {subgroup: {name: array}} (lazy)
        for c in CLASSES:
            if c not in self.f:
                continue
            cat = self.f[c]["catalog"]
            m = cat["is_galaxy"][()].astype(bool) & \
                cat["is_clean_galaxy"][()].astype(bool)
            rows = np.nonzero(m)[0]
            self.sel[c] = rows
            self.z[c] = cat["z_redrock"][()][rows]
            self.tid[c] = cat["targetid"][()][rows]
            self.prog[c] = cat["program"][()][rows]

    def chunk_rows(self, lo, hi):
        """class -> positions into self.sel[c] that fall in [lo, hi)."""
        out = {}
        for c in self.sel:
            p = np.nonzero((self.z[c] >= lo) & (self.z[c] < hi))[0]
            if len(p):
                out[c] = p
        return out

    def load_aux(self, c):
        """Read all row-aligned datasets of one class once and cache."""
        if c not in self.aux:
            d = {}
            for sg in ("catalog", "photometry", "quality"):
                d[sg] = {k: (v[()], dict(v.attrs))
                         for k, v in self.f[c][sg].items()}
            self.aux[c] = d
        return self.aux[c]


# ---------------------------------------------------------------- dry run
def dry_run(src, chunks, args):
    print("%3s  %-46s %-14s %7s %7s %7s %8s %10s" % (
        "idx", "file", "z range", "n_BGS", "n_ELG", "n_LRG", "total", "MB_raw"))
    tot = {c: 0 for c in CLASSES}
    for i, name, lo, hi in chunks:
        cr = chunk_rows(src, lo, hi)
        n = {c: len(cr.get(c, [])) for c in CLASSES}
        for c in CLASSES:
            tot[c] += n[c]
        t = sum(n.values())
        print("%3d  %-46s %-14s %7d %7d %7d %8d %10.1f" % (
            i, name, "[%g,%g)" % (lo, hi), n["BGS"], n["ELG"], n["LRG"], t,
            t * BYTES_PER_OBJ / 1e6))
    gt = sum(tot.values())
    print("%3s  %-46s %-14s %7d %7d %7d %8d %10.1f" % (
        "", "TOTAL (listed chunks)", "", tot["BGS"], tot["ELG"], tot["LRG"],
        gt, gt * BYTES_PER_OBJ / 1e6))
    # whole-sample statistics (all classes, not only listed chunks)
    nsel = sum(len(v) for v in src.sel.values())
    tids = np.concatenate([src.tid[c] for c in src.sel])
    progs = np.concatenate([src.prog[c] for c in src.sel])
    pairs = set(zip(tids.tolist(), [p.decode() for p in progs]))
    print("selected rows (all classes): %d; distinct targetid: %d; "
          "distinct (targetid, program): %d" % (
              nsel, len(np.unique(tids)), len(pairs)))
    if args.time_sample > 0:
        time_sample(src, args)


def chunk_rows(src, lo, hi):
    return src.chunk_rows(lo, hi)


def time_sample(src, args):
    rng = np.random.default_rng(0)
    pool_rows = [(c, int(r)) for c in src.sel for r in src.sel[c]]
    k = min(args.time_sample, len(pool_rows))
    pick = [pool_rows[i] for i in rng.choice(len(pool_rows), k, replace=False)]
    tasks = [(c, [r], None) for c, r in pick]   # one object per task: worst case
    ctx = mp.get_context("spawn")
    with ctx.Pool(args.workers, _worker_init, (src.path,)) as pool:
        pool.map(_noop, range(args.workers * 2))   # let workers start up
        t0 = time.time()
        for _ in pool.imap_unordered(_read_block, tasks):
            pass
        dt = time.time() - t0
    rate = k / dt
    tot = len(pool_rows)
    print("time sample: %d random objects, %d workers, %.2f s -> %.1f obj/s" % (
        k, args.workers, dt, rate))
    print("projected read time for all %d selected objects: %.1f min "
          "(single-object tasks; blocked reads may be faster)" % (
              tot, tot / rate / 60))


def _noop(x):
    return x


# ------------------------------------------------------------------ write
def block_tasks(rows_by_class, block, wave_ref):
    """Yield (cls, start, stop, task) in output order."""
    for c, srows in rows_by_class.items():
        for s in range(0, len(srows), block):
            e = min(s + block, len(srows))
            yield c, s, e, (c, [int(r) for r in srows[s:e]], wave_ref)


def write_chunk(src, pool, idx, name, lo, hi, args):
    final = os.path.join(args.out, name)
    part = final + ".partial"
    if os.path.exists(final) and not args.overwrite:
        print("[%d] %s exists, skipping" % (idx, name), flush=True)
        return
    cr = src.chunk_rows(lo, hi)
    if not cr:
        print("[%d] %s: no objects, skipping" % (idx, name), flush=True)
        return
    # source rows per class, in source order
    rows = {c: src.sel[c][p] for c, p in cr.items()}
    ntot = sum(len(r) for r in rows.values())
    print("[%d] %s: %d objects (%s)" % (idx, name, ntot, ", ".join(
        "%s=%d" % (c, len(r)) for c, r in rows.items())), flush=True)

    c0 = next(iter(rows))
    wave = src.f[c0]["spectra"]["obj_%05d" % rows[c0][0]]["wavelength"][()]
    assert wave.shape == (NWAVE,) and wave.dtype == np.float32

    meta = src.f["metadata"]
    with h5py.File(part, "w") as out:
        out.create_dataset("wavelength", data=wave)
        out["wavelength"].attrs["units"] = "Angstrom (vacuum, solar-system barycentric)"
        sm = out.create_group("source_metadata")
        for k, v in meta.attrs.items():
            sm.attrs[k] = v
        un = out.create_group("units")
        for k, v in meta["units"].attrs.items():
            un.attrs[k] = v
        un.attrs["resolution"] = RES_DESC
        a = out.attrs
        a["selection"] = SELECTION
        a["z_lo"] = float(lo)
        a["z_hi"] = float(hi)
        a["redshift_column"] = "z_redrock"
        a["bin_convention"] = "[z_lo, z_hi)"
        a["source_file"] = os.path.abspath(args.v4)
        a["source_md5"] = args.source_md5
        a["created"] = datetime.datetime.now().isoformat(timespec="seconds")
        a["script"] = os.path.basename(__file__)
        a["n_objects"] = ntot
        for c in CLASSES:
            a["n_" + c] = len(rows.get(c, []))

        dsets = {}
        for c, srows in rows.items():
            n = len(srows)
            g = out.create_group(c)
            for k, v in src.f[c].attrs.items():
                g.attrs[k] = v
            aux = src.load_aux(c)
            for sg, d in aux.items():
                gg = g.create_group(sg)
                for k, (arr, at) in d.items():
                    ds = gg.create_dataset(k, data=arr[srows])
                    for ak, av in at.items():
                        ds.attrs[ak] = av
            g.create_dataset("source_row", data=srows.astype("i8"))
            sp = g.create_group("spectra")
            kw = dict(compression="gzip", compression_opts=args.gzip,
                      shuffle=False)
            dsets[c] = {
                "flux": sp.create_dataset("flux", (n, NWAVE), "f4",
                                          chunks=(1, NWAVE), **kw),
                "ivar": sp.create_dataset("ivar", (n, NWAVE), "f4",
                                          chunks=(1, NWAVE), **kw),
                "mask": sp.create_dataset("mask", (n, NWAVE), "i2",
                                          chunks=(1, NWAVE), **kw),
                "resolution": sp.create_dataset(
                    "resolution", (n, NDIAG, NWAVE), "f4",
                    chunks=(1, NDIAG, NWAVE), **kw),
            }
            sa = g.create_group("spectra_attrs")
            for k, d in OBJ_ATTRS.items():
                dsets[c]["attr_" + k] = sa.create_dataset(k, (n,), d)

        # bounded producer so reads cannot run far ahead of the writer
        sem = threading.BoundedSemaphore(args.workers * 3)
        meta_list = []

        def gen():
            for c, s, e, task in block_tasks(rows, args.block, wave):
                sem.acquire()
                meta_list.append((c, s, e))
                yield task

        t0 = time.time()
        done = 0
        nblocks = 0
        for i, res in enumerate(pool.imap(_read_block, gen())):
            sem.release()
            c, s, e = meta_list[i]
            cls, flux, ivar, mask, resol, attrs, wave_ok = res
            assert cls == c
            if not wave_ok:
                raise RuntimeError("wavelength mismatch in %s rows %s"
                                   % (c, rows[c][s:e]))
            tid_cat = src.tid[c][cr[c][s:e]]
            if not np.array_equal(attrs["targetid"], tid_cat):
                raise RuntimeError("targetid attr != catalog targetid in %s "
                                   "block %d:%d" % (c, s, e))
            d = dsets[c]
            d["flux"][s:e] = flux
            d["ivar"][s:e] = ivar
            d["mask"][s:e] = mask
            d["resolution"][s:e] = resol
            for k in OBJ_ATTRS:
                d["attr_" + k][s:e] = attrs[k]
            done += e - s
            nblocks += 1
            if nblocks % 10 == 0 or done == ntot:
                dt = time.time() - t0
                print("    %d/%d objects, %.0f s, %.1f obj/s" % (
                    done, ntot, dt, done / dt), flush=True)
    os.replace(part, final)
    print("[%d] wrote %s (%.1f MB)" % (idx, final, os.path.getsize(final) / 1e6),
          flush=True)


# ------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    env = os.environ.get("FINAL_DESI_EXTRACTION")
    default_v4 = (os.path.join(env, "desi_dr1_extracted_spectra_v4.h5")
                  if env else None)
    ap.add_argument("--v4", default=default_v4, required=default_v4 is None,
                    help="source v4 HDF5 (default $FINAL_DESI_EXTRACTION/...)")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--time-sample", type=int, default=0,
                    help="dry run: time reading K random selected objects")
    ap.add_argument("--only", default=None,
                    help="comma-separated chunk indices to process")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--block", type=int, default=32,
                    help="objects per read task")
    ap.add_argument("--gzip", type=int, default=4, help="gzip level")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--source-md5", default=DEFAULT_MD5)
    args = ap.parse_args()

    chunks = chunk_list()
    if args.only:
        want = [int(x) for x in args.only.split(",")]
        bad = [w for w in want if not 0 <= w < len(chunks)]
        if bad:
            sys.exit("bad chunk indices: %s (valid 0..%d)" % (bad, len(chunks) - 1))
        chunks = [c for c in chunks if c[0] in want]

    t0 = time.time()
    src = Source(args.v4)
    print("read catalogs in %.1f s" % (time.time() - t0), flush=True)
    if args.dry_run:
        dry_run(src, chunks, args)
        return
    os.makedirs(args.out, exist_ok=True)
    ctx = mp.get_context("spawn")
    with ctx.Pool(args.workers, _worker_init, (args.v4,)) as pool:
        for idx, name, lo, hi in chunks:
            write_chunk(src, pool, idx, name, lo, hi, args)
    print("done in %.1f min" % ((time.time() - t0) / 60))


if __name__ == "__main__":
    main()
