"""Seeded random list of DESI DR1 main-survey HEALPix pixels in the DECaLS
(PHOTSYS='S') imaging region, plus clean-galaxy counts per class and redshift
chunk, from the local zpix v1 catalogues.  Provenance script of the committed pixel lists
(`pixel_lists/`, `batches/`); it is not part of the dgs workflow.

The selection replicates extract_desi_dr1_final.py exactly:
  - class from DESI_TARGET bits (l.61-64) with priority LRG > ELG > BGS
    (assign_class, l.107-115); rows with none of the three bits are dropped;
  - clean galaxy = SPECTYPE == 'GALAXY' & ZWARN == 0 & DELTACHI2 > 25
    (CLEAN_GALAXY_DCHI2 l.75; is_clean_galaxy l.449-451);
  - both programmes are walked, so a dark-programme fibre whose DESI_TARGET
    carries only BGS_ANY lands in BGS (the drop at l.302-319 applies only when
    no bright coadd exists).  Counted separately as 'BGS from dark'.
Redshift chunks reuse EDGES from the frozen packager (half-open).

The zpix files are read once each, as a raw memory map in row blocks, keeping
only the needed columns (the 20.7 GB tables never sit in RAM).

  python reference/pixel_list/build_desi_pixel_list.py --zpix-dir DIR --cache DIR --out DIR

--zpix-dir holds zpix-main-bright.fits, zpix-main-dark.fits and
redux_iron_zcatalog_v1.sha256sum (the DESI DR1 zcatalog/v1 directory).
"""
import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
from astropy.io import fits

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from desi_galaxy_spectra.frozen.package_desi_zchunks import EDGES  # noqa: E402

ZPIX_DIR = None         # set from --zpix-dir in main()
PROGRAMS = ("bright", "dark")
# extract_desi_dr1_final.py l.61-64, l.75
DESI_BIT_LRG = 1 << 0
DESI_BIT_ELG = 1 << 1
DESI_BIT_BGS_ANY = 1 << 60
CLEAN_GALAXY_DCHI2 = 25.0
CLASSES = ("BGS", "ELG", "LRG")          # codes 1, 2, 3 below
EXISTING_22 = (21852, 21853, 21854, 21855, 21860, 21861, 21863,
               21872, 21873, 21874, 21875, 21876, 21877, 21878, 21879,
               21880, 21881, 21884, 40954, 40955, 40958, 40959)
COLS = ("TARGETID", "HEALPIX", "Z", "ZWARN", "SPECTYPE", "DELTACHI2",
        "COADD_FIBERSTATUS", "PHOTSYS", "DESI_TARGET", "ZCAT_PRIMARY")
MIN_FRAC_S = 0.95
SEED = 20261023
N_DRAW = 100
MB_COADD = {"bright": 322.0, "dark": 406.0}   # plan section 0, 400 sampled pixels
MB_PER_OBJ_PACKAGED = 0.354                   # v4_zchunks ratio
THIN = 100


def chunk_names():
    out = []
    for lo, hi in zip(EDGES[:-1], EDGES[1:]):
        out.append("z<%.1f" % hi if lo == -np.inf else
                   "z>=%.1f" % lo if hi == np.inf else "%.1f-%.1f" % (lo, hi))
    return out


def scan(program, block):
    """One sequential pass over a zpix file; return compact per-row arrays."""
    path = ZPIX_DIR / f"zpix-main-{program}.fits"
    with fits.open(path, memmap=True) as hdul:
        hdu = hdul[1]
        dtype = hdu.columns.dtype.newbyteorder(">")   # FITS is big-endian
        n = hdu.header["NAXIS2"]
        off = hdu._data_offset
    mm = np.memmap(path, dtype=dtype, mode="r", offset=off, shape=(n,))
    keep = {k: [] for k in ("tid", "hpx", "cls", "z", "clean", "S", "zprim",
                            "fstat")}
    all_hpx = []
    for s in range(0, n, block):
        b = mm[s:s + block]
        hpx = b["HEALPIX"].astype(np.int64)
        all_hpx.append(np.unique(hpx))
        dt = b["DESI_TARGET"].astype(np.int64)
        cls = np.zeros(len(b), np.int8)
        cls[(dt & DESI_BIT_BGS_ANY) != 0] = 1
        cls[(dt & DESI_BIT_ELG) != 0] = 2
        cls[(dt & DESI_BIT_LRG) != 0] = 3        # last write wins: LRG>ELG>BGS
        m = cls > 0
        spectype = np.char.strip(b["SPECTYPE"][m])
        clean = ((spectype == b"GALAXY") & (b["ZWARN"][m].astype(np.int64) == 0)
                 & (b["DELTACHI2"][m].astype(np.float64) > CLEAN_GALAXY_DCHI2))
        zp = b["ZCAT_PRIMARY"][m]
        zp = (zp == ord("T")) if zp.dtype.kind in "iu" else zp.astype(bool)
        keep["tid"].append(b["TARGETID"][m].astype(np.int64))
        keep["hpx"].append(hpx[m])
        keep["cls"].append(cls[m])
        keep["z"].append(b["Z"][m].astype(np.float64))
        keep["clean"].append(clean)
        keep["S"].append(np.char.strip(b["PHOTSYS"][m]) == b"S")
        keep["zprim"].append(zp)
        keep["fstat"].append(b["COADD_FIBERSTATUS"][m].astype(np.int64))
        if s == 0:
            assert np.all((hpx >= 0) & (hpx < 12 * 64 ** 2)), "HEALPIX out of nside-64 range"
        print(f"  {program}: {min(s + block, n):,}/{n:,}", flush=True)
    out = {k: np.concatenate(v) for k, v in keep.items()}
    out["all_hpx"] = np.unique(np.concatenate(all_hpx))
    out["n_rows"] = np.array(n)
    return out


def load(cache, block):
    data = {}
    for p in PROGRAMS:
        f = cache / f"zpix_{p}_compact.npz"
        if not f.exists():
            np.savez(f, **scan(p, block))
        data[p] = dict(np.load(f))
    return data


def count_table(data, pixels, version, per_object_S):
    """counts[class][chunk] over both programmes, plus BGS-from-dark."""
    pix = np.asarray(sorted(pixels))
    nch = len(EDGES) - 1
    tab = {c: np.zeros(nch, np.int64) for c in CLASSES}
    bgs_dark = 0
    for p in PROGRAMS:
        d = data[p]
        m = d["clean"] & np.isin(d["hpx"], pix)
        if version == "plus_primary_fiberstatus":
            m &= d["zprim"] & (d["fstat"] == 0)
        if per_object_S:
            m &= d["S"]
        ich = np.searchsorted(EDGES, d["z"][m], side="right") - 1
        for k, c in enumerate(CLASSES, 1):
            sel = d["cls"][m] == k
            tab[c] += np.bincount(ich[sel], minlength=nch)
            if p == "dark" and c == "BGS":
                bgs_dark += int(sel.sum())
    return tab, bgs_dark


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    global ZPIX_DIR
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--zpix-dir", type=Path, required=True,
                    help="DESI DR1 zcatalog/v1 directory (zpix-main-{bright,dark}.fits and "
                         "redux_iron_zcatalog_v1.sha256sum)")
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--block", type=int, default=200_000)
    args = ap.parse_args()
    ZPIX_DIR = args.zpix_dir
    args.cache.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=True)
    data = load(args.cache, args.block)

    # ---- per-pixel PHOTSYS fraction over clean, class-tagged galaxies
    both = np.intersect1d(data["bright"]["all_hpx"], data["dark"]["all_hpx"])
    hp = np.concatenate([data[p]["hpx"][data[p]["clean"]] for p in PROGRAMS])
    sS = np.concatenate([data[p]["S"][data[p]["clean"]] for p in PROGRAMS])
    upix, inv = np.unique(hp, return_inverse=True)
    n_clean = np.bincount(inv)
    n_S = np.bincount(inv, weights=sS).astype(np.int64)
    frac = n_S / n_clean
    in_both = np.isin(upix, both)
    is_old = np.isin(upix, EXISTING_22)
    elig = in_both & (frac >= MIN_FRAC_S) & ~is_old
    eligible = upix[elig]
    assert np.all(np.isin(EXISTING_22, upix[in_both])), "existing 22 not all found"

    rng = np.random.default_rng(SEED)
    drawn = np.sort(rng.choice(eligible, size=N_DRAW, replace=False))

    # ---- write the list + per-pixel table
    lst = args.out / "dr1_decals_random100.txt"
    lst.write_text("# DESI DR1 main HEALPix (nside 64, nested), seed %d; see "
                   "dr1_decals_random100.json\n" % SEED
                   + "\n".join(str(int(x)) for x in drawn) + "\n")
    with open(args.out / "pixel_photsys_table.csv", "w") as f:
        f.write("healpix,n_clean_galaxies,n_photsys_S,frac_S,in_both_programs,"
                "existing_22,eligible,drawn\n")
        dset = set(drawn.tolist())
        for i, px in enumerate(upix):
            f.write("%d,%d,%d,%.6f,%d,%d,%d,%d\n" % (
                px, n_clean[i], n_S[i], frac[i], in_both[i], is_old[i],
                elig[i], px in dset))

    # ---- counts
    samples = {"drawn100": drawn, "existing22": np.array(EXISTING_22),
               "all_eligible": eligible}
    results = {}
    with open(args.out / "counts_class_chunk.csv", "w") as f:
        f.write("sample,version,per_object_photsys_S,class,chunk,count\n")
        for sname, pix in samples.items():
            for version in ("extractor_cut", "plus_primary_fiberstatus"):
                for perS in (False, True):
                    tab, bgsd = count_table(data, pix, version, perS)
                    key = f"{sname}|{version}|{'S' if perS else 'all'}"
                    results[key] = {"table": {c: tab[c].tolist() for c in CLASSES},
                                    "bgs_from_dark": bgsd,
                                    "total": int(sum(tab[c].sum() for c in CLASSES))}
                    for c in CLASSES:
                        for ch, v in zip(chunk_names(), tab[c]):
                            f.write(f"{sname},{version},{perS},{c},{ch},{v}\n")

    # ---- provenance sidecar
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain",
                            "--", "reference/pixel_list/build_desi_pixel_list.py"],
                           capture_output=True, text=True).stdout.strip()
    sums = dict(line.split()[::-1] for line in
                (ZPIX_DIR / "redux_iron_zcatalog_v1.sha256sum").read_text()
                .splitlines() if "zpix-main-" in line)
    fs = frac[elig]
    side = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "seed": SEED, "rng": "numpy.random.default_rng(seed).choice(eligible "
        "sorted ascending, size=100, replace=False), output sorted",
        "numpy_version": np.__version__, "n_drawn": N_DRAW,
        "eligibility_rule": (
            "HEALPix pixel present in both zpix-main-bright and zpix-main-dark "
            "(i.e. a bright and a dark coadd exist); among its clean, class-tagged "
            "galaxies (both programmes; extractor cut) the fraction with "
            f"PHOTSYS='S' is >= {MIN_FRAC_S}; not one of the 22 v4 pixels"),
        "n_eligible": int(elig.sum()),
        "n_pixels_both_programmes": int(len(both)),
        "frac_S_eligible": {"min": float(fs.min()), "median": float(np.median(fs)),
                            "n_exactly_1": int((fs == 1).sum())},
        "excluded_existing_22": list(EXISTING_22),
        "inputs": {f"zpix-main-{p}.fits": {
            "path": str(ZPIX_DIR / f"zpix-main-{p}.fits"),
            "sha256": sums[f"zpix-main-{p}.fits"],
            "sha256_source": "redux_iron_zcatalog_v1.sha256sum, sha256sum -c "
                             "PASS 2026-10-22 18:27 (SHA256_OK)",
            "n_rows": int(data[p]["n_rows"])} for p in PROGRAMS},
        "script": "reference/pixel_list/build_desi_pixel_list.py",
        "script_sha256": sha256(Path(__file__)),
        "repo_head": head,
        "script_committed_at_head": not dirty,
        "chunk_edges": [None if not np.isfinite(e) else e for e in EDGES],
        "pixels": drawn.tolist(),
    }
    (args.out / "dr1_decals_random100.json").write_text(json.dumps(side, indent=1))
    (args.out / "counts_summary.json").write_text(json.dumps(
        {"chunks": chunk_names(), "results": results}, indent=1))
    print(json.dumps({k: v for k, v in side.items() if k != "pixels"}, indent=1))


if __name__ == "__main__":
    main()
