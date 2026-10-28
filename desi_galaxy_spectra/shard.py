"""Per-pixel extraction (unmodified extractor) and restacking into a shard.

Per pixel: collect_rows + write_hdf5 (imported, unmodified) write a temp v4-layout file
(one group per object); it is restacked into ``healpix_<pix>.h5`` with the packager's
``_read_block``; the temp file is deleted once the shard has been read back and compared.
"""
from __future__ import annotations

import os
import platform
import shutil
import time
from pathlib import Path

from . import config
from .fetch import sha256_file

import h5py
import numpy as np
from astropy.io import fits

from .frozen import extract_desi_dr1_final as ext          # frozen extractor
from .frozen import package_desi_zchunks as pk             # frozen packager

CLASSES = pk.CLASSES
NWAVE, NDIAG, OBJ_ATTRS = pk.NWAVE, pk.NDIAG, pk.OBJ_ATTRS
BLOCK = 32
GZIP = 4


class ShardError(RuntimeError):
    pass


def _log_default(msg):
    print(msg, flush=True)


# ------------------------------------------------------------------ extract
def extract_to_temp(coadd_files, redrock_dir, temp_path, log=_log_default):
    """Run the unmodified extractor on the given coadds.  Returns the number of rows."""
    coadd_files = sorted(Path(p) for p in coadd_files)
    rows = ext.collect_rows(coadd_files, Path(redrock_dir), False, log)
    if not rows:
        raise ShardError("extractor collected no rows")
    programs = {r["program"] for r in rows}
    # Ruling: BGS-from-dark rows are kept.  The extractor drops every BGS-tagged row if no bright
    # row survives (write_hdf5 has_bright), so both programmes must have contributed rows.
    if programs != set(config.PROGRAMS):
        raise ShardError(f"programs with extracted rows are {sorted(programs)}, expected "
                         f"{sorted(config.PROGRAMS)}: the extractor would drop BGS-from-dark rows")
    ext.write_hdf5(rows, Path(temp_path), n_coadds=len(coadd_files), log=log,
                   programs_processed=programs)
    check_bgs_kept(temp_path)
    return len(rows)


def check_bgs_kept(temp_path):
    """Hard error unless metadata@bgs_group_skipped is present and False."""
    with h5py.File(temp_path, "r") as h:
        v = h["metadata"].attrs.get("bgs_group_skipped")
    if v is None or bool(v) is not False:
        raise ShardError(f"metadata@bgs_group_skipped is {v!r}: BGS-from-dark rows were dropped")


def check_extraction_success(h):
    """Any failed extraction is a hard error."""
    for c in CLASSES:
        if c not in h:
            continue
        ok = h[c]["quality/extraction_success"][()]
        if not ok.all():
            bad = np.nonzero(~ok)[0]
            raise ShardError(f"{c}: {len(bad)} failed extractions (rows {bad[:10].tolist()}...)")
        for i in range(len(ok)):
            if not h[c]["spectra"][f"obj_{i:05d}"].attrs["extraction_success"]:
                raise ShardError(f"{c} obj_{i:05d}: extraction_success False")


# ------------------------------------------------------------------ PHOTSYS
def read_photsys(h, coadd_by_prog):
    """PHOTSYS per object, read from the coadd FIBERMAP at fiber_index, targetid asserted.
    Returns {class: array S1}."""
    cache = {}
    out = {}
    for c in CLASSES:
        if c not in h:
            continue
        cat = h[c]["catalog"]
        tid, fi, prog = cat["targetid"][()], cat["fiber_index"][()], cat["program"][()]
        ps = np.empty(len(tid), dtype="S1")
        for i in range(len(tid)):
            p = prog[i].decode()
            if p not in cache:
                with fits.open(coadd_by_prog[p], memmap=True) as hd:
                    fm = hd["FIBERMAP"].data
                    cache[p] = (np.asarray(fm["TARGETID"]).copy(),
                                np.char.strip(np.asarray(fm["PHOTSYS"]).astype("U8")))
            tids, phot = cache[p]
            j = int(fi[i])
            if int(tids[j]) != int(tid[i]):
                raise ShardError(f"{c} row {i}: FIBERMAP TARGETID {tids[j]} != catalog {tid[i]}")
            ps[i] = phot[j].encode("ascii")[:1] if phot[j] else b""
        out[c] = ps
    return out


# ------------------------------------------------------------------ restack
def _read_all_blocks(path, cls, n, wave_ref, block=BLOCK):
    """Generator of (s, e, flux, ivar, mask, res, attrs) using the packager's _read_block."""
    pk._worker_init(str(path))
    try:
        for s in range(0, n, block):
            e = min(s + block, n)
            c, flux, ivar, mask, res, attrs, wave_ok = pk._read_block(
                (cls, list(range(s, e)), wave_ref))
            if not wave_ok:
                raise ShardError(f"{cls} rows {s}:{e}: wavelength differs from reference")
            yield s, e, flux, ivar, mask, res, attrs
    finally:
        pk._W["f"].close()
        pk._W.pop("f", None)


def restack(temp_path, shard_path, pix, photsys, input_files, log=_log_default,
            coadd_dirs=None):
    """temp v4-layout -> shard (written as .partial, then renamed).  Returns shard path."""
    shard_path = Path(shard_path)
    part = shard_path.with_name(shard_path.name + ".partial")
    with h5py.File(temp_path, "r") as src:
        check_extraction_success(src)
        c0 = next(c for c in CLASSES if c in src)
        wave = src[c0]["spectra"]["obj_00000"]["wavelength"][()]
        if wave.shape != (NWAVE,) or wave.dtype != np.float32:
            raise ShardError(f"unexpected wavelength {wave.shape} {wave.dtype}")
        with h5py.File(part, "w") as out:
            out.create_dataset("wavelength", data=wave)
            out["wavelength"].attrs["units"] = "Angstrom (vacuum, solar-system barycentric)"
            src.copy("metadata", out)
            a = out.attrs
            a["healpix"] = int(pix)
            a["kit_version"] = config.KIT_VERSION
            a["desispec_version"] = _desispec_version()
            a["numpy_version"] = np.__version__
            a["python_version"] = platform.python_version()
            a["lockfile_sha256"] = config.lockfile_sha256()
            a["input_files"] = np.array([n for n, _ in input_files], dtype="S")
            a["input_sha256"] = np.array([s for _, s in input_files], dtype="S")
            for c in CLASSES:
                if c not in src:
                    continue
                g_src = src[c]
                n = int(g_src.attrs["n_objects"])
                g = out.create_group(c)
                for k, v in g_src.attrs.items():
                    g.attrs[k] = v
                for sg in ("catalog", "photometry", "quality"):
                    src.copy(g_src[sg], g, name=sg)
                if len(photsys[c]) != n:
                    raise ShardError(f"{c}: photsys length mismatch")
                g["catalog"].create_dataset("photsys", data=photsys[c])
                if coadd_dirs:      # raw files will live elsewhere than where they were read
                    _rewrite_coadd_file(g["catalog"], coadd_dirs)
                sp = g.create_group("spectra")
                kw = dict(compression="gzip", compression_opts=GZIP, shuffle=False)
                d = {
                    "flux": sp.create_dataset("flux", (n, NWAVE), "f4", chunks=(1, NWAVE), **kw),
                    "ivar": sp.create_dataset("ivar", (n, NWAVE), "f4", chunks=(1, NWAVE), **kw),
                    "mask": sp.create_dataset("mask", (n, NWAVE), "i2", chunks=(1, NWAVE), **kw),
                    "resolution": sp.create_dataset("resolution", (n, NDIAG, NWAVE), "f4",
                                                    chunks=(1, NDIAG, NWAVE), **kw),
                }
                sa = g.create_group("spectra_attrs")
                for k, dt in OBJ_ATTRS.items():
                    d["attr_" + k] = sa.create_dataset(k, (n,), dt)
                tid_cat = g["catalog/targetid"][()]
                for s, e, flux, ivar, mask, res, attrs in _read_all_blocks(
                        temp_path, c, n, wave):
                    if not np.array_equal(attrs["targetid"], tid_cat[s:e]):
                        raise ShardError(f"{c} rows {s}:{e}: spectra targetid != catalog")
                    d["flux"][s:e] = flux
                    d["ivar"][s:e] = ivar
                    d["mask"][s:e] = mask
                    d["resolution"][s:e] = res
                    for k in OBJ_ATTRS:
                        d["attr_" + k][s:e] = attrs[k]
                log(f"  shard {c}: {n} objects stacked")
    check_shard_against_temp(temp_path, part, coadd_dirs=coadd_dirs)
    os.replace(part, shard_path)
    return shard_path


def _rewrite_coadd_file(cat, coadd_dirs):
    """Replace the directory part of catalog/coadd_file by the permanent raw directory."""
    old = cat["coadd_file"][()]
    prog = cat["program"][()]
    new = np.array([(str(Path(coadd_dirs[p.decode()]) / Path(o.decode()).name)).encode()
                    for o, p in zip(old, prog)], dtype=old.dtype)
    cat["coadd_file"][...] = new


def check_shard_against_temp(temp_path, shard_path, log=_log_default, coadd_dirs=None):
    """Read the shard back and compare it with the temp file (bytes), row by row."""
    with h5py.File(shard_path, "r") as sh, h5py.File(temp_path, "r") as tp:
        if not np.array_equal(sh["wavelength"][()],
                              tp[next(c for c in CLASSES if c in tp)]["spectra/obj_00000/wavelength"][()]):
            raise ShardError("shard wavelength != temp wavelength")
        for c in CLASSES:
            if (c in sh) != (c in tp):
                raise ShardError(f"class {c} present in only one of shard/temp")
            if c not in tp:
                continue
            n = int(tp[c].attrs["n_objects"])
            for sg in ("catalog", "photometry", "quality"):
                for k, ds in tp[c][sg].items():
                    a, b = ds[()], sh[c][sg][k][()]
                    if coadd_dirs and sg == "catalog" and k == "coadd_file":
                        p_ = tp[c]["catalog/program"][()]
                        want = [str(Path(coadd_dirs[q.decode()]) / Path(o.decode()).name)
                                for o, q in zip(a, p_)]
                        if [x.decode() for x in b] != want:
                            raise ShardError(f"{c}/catalog/coadd_file: unexpected rewrite")
                        continue
                    if a.dtype != b.dtype or a.shape != b.shape or \
                            a.tobytes() != b.tobytes():
                        raise ShardError(f"{c}/{sg}/{k}: shard != temp")
            pk._worker_init(str(temp_path))
            try:
                for s in range(0, n, BLOCK):
                    e = min(s + BLOCK, n)
                    _, flux, ivar, mask, res, attrs, _ = pk._read_block(
                        (c, list(range(s, e)), None))
                    sp = sh[c]["spectra"]
                    for name, arr in (("flux", flux), ("ivar", ivar), ("mask", mask),
                                      ("resolution", res)):
                        if sp[name][s:e].tobytes() != arr.tobytes():
                            raise ShardError(f"{c}/spectra/{name} rows {s}:{e}: shard != temp")
                    for k in OBJ_ATTRS:
                        if sh[c]["spectra_attrs"][k][s:e].tobytes() != attrs[k].tobytes():
                            raise ShardError(f"{c}/spectra_attrs/{k} rows {s}:{e}: shard != temp")
            finally:
                pk._W["f"].close()
                pk._W.pop("f", None)


def _desispec_version():
    try:
        import desispec
        return str(desispec.__version__)
    except Exception:       # noqa: BLE001
        return "unknown"


# ------------------------------------------------------------------ driver
def check_inputs_against_sha256sum(pix, coadd_by_prog, redrock_by_prog, hashes):
    """hashes: {path: sha256} just computed from the files the extractor read.  Compare them with
    the DESI .sha256sum kept beside each raw file (free: the hashes are computed anyway), so a
    SHA256_OK marker is never trusted alone.  Raises ShardError on a missing .sha256sum, an
    unlisted file or a mismatch."""
    from .fetch import parse_sha256sum
    for prog in coadd_by_prog:
        for p in (coadd_by_prog[prog], redrock_by_prog[prog]):
            p = Path(p)
            sfile = p.parent / config.sha_name(pix, prog)
            if not sfile.exists():
                raise ShardError(f"{sfile}: DESI .sha256sum missing, cannot verify {p.name}")
            want = parse_sha256sum(sfile.read_text()).get(p.name)
            if want is None:
                raise ShardError(f"{p.name} not listed in {sfile.name}")
            if hashes[str(p)] != want:
                raise ShardError(f"{p}: SHA256 {hashes[str(p)]} != {want} (listed in {sfile.name})")


def process_pixel(pix, coadd_by_prog, redrock_by_prog, shard_dir, scratch,
                  keep_temp=False, log=_log_default, coadd_dirs=None, verify_sha=False):
    """Extract one pixel (all given programs) to <shard_dir>/healpix_<pix>.h5.
    Returns a result dict with timings.  Does not move or delete the raw inputs.
    verify_sha: check the hashes of the input files against the DESI .sha256sum beside them
    (raw-root mode); the gate's flat directory has no .sha256sum files and passes False."""
    shard_dir, scratch = Path(shard_dir), Path(scratch)
    work = scratch / f"p{int(pix)}"
    if work.exists():
        shutil.rmtree(work)
    inp = work / "in"
    inp.mkdir(parents=True)
    coadds = []
    for prog, p in coadd_by_prog.items():
        coadds.append(Path(p).absolute())       # real path: recorded in catalog/coadd_file
        (inp / config.redrock_name(pix, prog)).symlink_to(Path(redrock_by_prog[prog]).resolve())
    temp = work / f"v4layout_{int(pix)}.h5"
    t0 = time.time()
    n_rows = extract_to_temp(coadds, inp, temp, log=log)
    t_extract = time.time() - t0
    t1 = time.time()
    input_files, hashes = [], {}
    for prog in coadd_by_prog:
        for p in (coadd_by_prog[prog], redrock_by_prog[prog]):
            hashes[str(Path(p))] = sha256_file(p)
            input_files.append((Path(p).name, hashes[str(Path(p))]))
    if verify_sha:
        check_inputs_against_sha256sum(pix, coadd_by_prog, redrock_by_prog, hashes)
    with h5py.File(temp, "r") as h:
        ps = read_photsys(h, {k: v for k, v in coadd_by_prog.items()})
    shard_dir.mkdir(parents=True, exist_ok=True)
    shard = shard_dir / config.shard_name(pix)
    restack(temp, shard, pix, ps, input_files, log=log, coadd_dirs=coadd_dirs)
    t_shard = time.time() - t1
    kept = None
    if keep_temp:
        kept = shard_dir / temp.name
        shutil.move(str(temp), kept)
    shutil.rmtree(work)
    import resource
    return {
        "healpix": int(pix), "n_objects": int(n_rows),
        "seconds_extract": t_extract, "seconds_shard": t_shard,
        "seconds_total": t_extract + t_shard,
        "peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0,
        "shard": str(shard), "shard_bytes": shard.stat().st_size,
        "kept_temp": str(kept) if kept else None,
    }


# ------------------------------------------------------------------ job runner
def run_pixel_job(job):
    """One pixel end to end (module-level so a process pool can call it).

    job keys: pix, shard_dir, staging, keep_temp, and either local_flat + redrock_dir (gate
    mode, nothing downloaded or moved) or raw_root (+ fetch: stream the download into staging,
    extract, then move the raw files to raw_root).
    """
    from .fetch import Fetcher, FetchError, free_bytes, move_verified
    pix = int(job["pix"])
    shard_dir, staging = Path(job["shard_dir"]), Path(job["staging"])
    fetch_mode = bool(job.get("fetch")) and not job.get("local_flat")
    if fetch_mode and not str(job.get("raw_root") or ""):
        raise ShardError("--fetch needs --raw-root (the permanent raw archive)")
    if (shard_dir / config.shard_name(pix)).exists():
        res = {"healpix": pix, "skipped": True}
        if fetch_mode:
            # The shard is complete, but its raw archive may not be: finish or redo the move.
            res["raw_archive"] = ensure_raw_archive(pix, job["raw_root"], staging, Fetcher())
        return res
    scratch = staging / "work"
    scratch.mkdir(parents=True, exist_ok=True)
    moved = None
    if job.get("local_flat"):
        coadds = {p: Path(job["local_flat"]) / config.coadd_name(pix, p) for p in config.PROGRAMS}
        reds = {p: Path(job["redrock_dir"]) / config.redrock_name(pix, p) for p in config.PROGRAMS}
        dirs = None
    else:
        raw_root = Path(job["raw_root"])
        if job.get("fetch"):
            fetcher = Fetcher()
            need = 0
            for prog, kind, fn, url in config.pixel_urls(pix):
                status, size = fetcher.head(url)
                if status != 200 or size is None:
                    raise FetchError(f"{url}: HEAD status {status}")
                need += size
            if free_bytes(staging) < need + config.STAGING_MARGIN_BYTES:
                raise ShardError(f"pixel {pix}: staging has {free_bytes(staging)} B free, "
                                 f"needs {need + config.STAGING_MARGIN_BYTES} B")
            dirs = fetcher.fetch_pixel(pix, staging / "raw")
        else:
            dirs = {p: config.raw_dir(raw_root, pix, p) for p in config.PROGRAMS}
            for p, d in dirs.items():
                if not (d / config.SHA_MARKER).exists():
                    raise ShardError(f"{d}: no {config.SHA_MARKER} (run `dgs fetch` first)")
                if not (d / config.sha_name(pix, p)).exists():
                    raise ShardError(f"{d}: DESI .sha256sum missing (re-run `dgs fetch`)")
        coadds = {p: dirs[p] / config.coadd_name(pix, p) for p in config.PROGRAMS}
        reds = {p: dirs[p] / config.redrock_name(pix, p) for p in config.PROGRAMS}
    for p in list(coadds.values()) + list(reds.values()):
        if not Path(p).exists():
            raise ShardError(f"missing input {p}")
    res = process_pixel(pix, coadds, reds, shard_dir, scratch,
                        keep_temp=bool(job.get("keep_temp")),
                        coadd_dirs=({p: str(config.raw_dir(raw_root, pix, p))
                                     for p in config.PROGRAMS} if job.get("fetch") else None),
                        verify_sha=dirs is not None)
    if dirs is not None and job.get("fetch"):
        # move to the raw archive, re-hash there, marker last; raises (pixel fails) if not verified
        moved = []
        for prog, d in dirs.items():
            moved += move_verified(d, config.raw_dir(raw_root, pix, prog), pix, prog)
    res["raw_files_moved"] = moved
    return res


def ensure_raw_archive(pix, raw_root, staging, fetcher, log=_log_default):
    """Make sure the permanent raw directories of `pix` are complete and hash-verified.

    For each programme: if the destination verifies (marker + files + sha256 re-computed), only a
    leftover staged copy is removed; else a staged copy is verified (or re-fetched, resumable,
    when missing/incomplete/corrupt) and moved with the destination re-hash.  Returns
    {prog: "verified" | "moved" | "refetched"}; raises if a destination cannot be verified.
    """
    from .fetch import FetchError, dest_verified, move_verified, prog_names
    status = {}
    for prog in config.PROGRAMS:
        dest = config.raw_dir(raw_root, pix, prog)
        staged = config.raw_dir(Path(staging) / "raw", pix, prog)
        if dest_verified(dest, pix, prog):
            if staged.exists():
                shutil.rmtree(staged)
            status[prog] = "verified"
            continue
        how = "moved"
        if staged.exists() and (staged / config.SHA_MARKER).exists():
            try:                      # a marked staged copy must still hash correctly
                fetcher.verify_sha(staged, prog_names(pix, prog))
            except (FetchError, OSError):
                shutil.rmtree(staged)
                how = "refetched"
        elif staged.exists():
            how = "refetched"
        if how == "refetched" or not staged.exists():
            how = "refetched"
            fetcher.fetch_pixel(pix, Path(staging) / "raw", log=log, progs=[prog])
        move_verified(staged, dest, pix, prog, log=log)
        status[prog] = how
    return status
