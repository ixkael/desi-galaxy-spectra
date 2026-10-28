#!/usr/bin/env python3
"""
DESI DR1 Spectra Package Builder (v3.0)
========================================

Build a self-describing HDF5 file containing DESI DR1 spectra, Redrock
redshifts, FIBERMAP photometry, and SCORES quality metrics, for all
BGS / ELG / LRG targets present in a set of local DR1 coadd files.

v3.0 (2026-06-02) changes:
  - Top-level groups (BGS / ELG / LRG) still reflect DESI_TARGET targeting
    class, BUT each catalog now carries boolean truth columns derived from
    Redrock SPECTYPE (`is_galaxy`, `is_qso`, `is_star`, plus quality-cut
    `is_clean_galaxy`/`is_clean_qso`/`is_clean_star`) and targeting-bit
    columns (`is_lrg_target`, `is_elg_target`, `is_qso_target`,
    `is_bgs_target`, `desi_target`). Downstream code should filter on
    `is_clean_galaxy` (and AND with a targeting bit for a single selection
    function) — group membership alone is targeting-class, NOT truth.
  - BGS group is suppressed when no main/bright coadds are processed;
    real BGS targets are only observed in bright, so a BGS group built
    from dark-only input would consist of QSO/STAR contaminants with the
    BGS_ANY bit also set.
  - Fixed pixgroup convention in the Redrock download URL
    (healpix // 100, not (healpix // 100) * 100).
  - Removed misleading `z_mock` alias (v1/v2 had it as an alias to
    `z_redrock`); use `z_redrock` directly.

v2.0 (2026-05-22) was a rewrite of v1.0 (2026-03-31) that replaced mock
redshifts with real Redrock measurements and added FIBERMAP photometry +
SCORES quality. Spectra layout ({GROUP}/spectra/obj_NNNNN, 1D B+R+Z
concatenation) and basic catalog/photometry/quality field set are
unchanged from v2.0.

Inputs (defaults relative to repo root):
  --coadd-dir    pop-cosmos-KIDS_DESI/desi_dr1_healpix/coadd-*.fits
  --redrock-dir  data/redrock_cache/redrock-*.fits

Missing redrock files are fetched from the DESI DR1 public release at
data.desi.lbl.gov, unless --skip-download is set.

Outputs:
  --output       final_desi_extraction/desi_dr1_extracted_spectra_v3.h5
"""

import argparse
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import h5py
import numpy as np
from astropy.io import fits

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
from popfit.paths import DESI_DR1_HEALPIX, FINAL_DESI_EXTRACTION

# DESI_TARGET bits (main survey; see desitarget.targetmask)
DESI_BIT_LRG = 1 << 0
DESI_BIT_ELG = 1 << 1
DESI_BIT_QSO = 1 << 2
DESI_BIT_BGS_ANY = 1 << 60

# Priority used when a single fiber matches more than one of the three targeting
# classes. Note: this is *targeting* class; Redrock SPECTYPE is stored separately
# (`spectype`/`subtype`) and the boolean `is_galaxy` / `is_qso` / `is_star`
# columns in each catalog let downstream code filter on spectral truth.
GROUP_PRIORITY = ("LRG", "ELG", "BGS")

# DESI Y1 redshift-quality thresholds used by the `is_clean_*` columns.
# Galaxies require zwarn==0 AND deltachi2 > 25; QSOs use deltachi2 > 40.
# See Adame+24 (DESI Y1 KP) for the rationale.
CLEAN_GALAXY_DCHI2 = 25.0
CLEAN_QSO_DCHI2 = 40.0

# DECam g/r/z and WISE W1/W2 extinction coefficients A_lambda / E(B-V)
# (Schlafly+11 recalibration of SFD98, with Yuan+13 for WISE; the standard
# DESI/Legacy Survey convention).
EXTINCTION_COEFFS = {
    "g": 3.214,
    "r": 2.165,
    "z": 1.211,
    "W1": 0.184,
    "W2": 0.113,
}

REDROCK_URL = (
    "https://data.desi.lbl.gov/public/dr1/spectro/redux/iron/healpix/"
    "{survey}/{program}/{group}/{healpix}/"
    "redrock-{survey}-{program}-{healpix}.fits"
)

DESI_TYPICAL_RESOLUTION = (
    3000.0  # lambda/dlambda; kept for quality/spectral_resolution backward compat
)

SCRIPT_VERSION = "4.0.0"


def parse_coadd_name(path):
    parts = path.stem.split("-")
    return {"survey": parts[1], "program": parts[2], "healpix": int(parts[3])}


def assign_class(desi_target):
    t = int(desi_target)
    if t & DESI_BIT_LRG:
        return "LRG"
    if t & DESI_BIT_ELG:
        return "ELG"
    if t & DESI_BIT_BGS_ANY:
        return "BGS"
    return None


def download_redrock(meta, redrock_dir, log):
    fname = f"redrock-{meta['survey']}-{meta['program']}-{meta['healpix']}.fits"
    fpath = redrock_dir / fname
    if fpath.exists():
        return fpath
    group = meta["healpix"] // 100
    url = REDROCK_URL.format(group=group, **meta)
    log(f"    downloading {fname} from {url}")
    try:
        redrock_dir.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(url, fpath)
        size_mb = fpath.stat().st_size / 1e6
        log(f"    downloaded {size_mb:.1f} MB")
        return fpath
    except Exception as exc:
        log(f"    download FAILED ({exc})")
        if fpath.exists():
            fpath.unlink()
        return None


def extract_one_spectrum(hdul, fiber_index):
    """Merge B+R+Z arms for one fiber using desispec coadd_cameras.

    Returns (wave, flux, ivar, mask, resolution, coverage) where resolution
    has shape (n_diag, n_wave) — the raw diagonal bands of the sparse LSF matrix.
    """
    try:
        from desispec.coaddition import coadd_cameras
        from desispec.spectra import Spectra
    except ImportError as exc:
        raise ImportError(
            "desispec is required for v4 extraction. "
            "Install with: conda install -c conda-forge desispec  "
            "or: pip install desispec"
        ) from exc

    wave_d, flux_d, ivar_d, mask_d, res_d = {}, {}, {}, {}, {}
    coverage = {}
    for arm in ("b", "r", "z"):
        B = arm.upper()
        w = hdul[f"{B}_WAVELENGTH"].data
        fl = hdul[f"{B}_FLUX"].data[fiber_index : fiber_index + 1]
        iv = hdul[f"{B}_IVAR"].data[fiber_index : fiber_index + 1]
        mk = hdul[f"{B}_MASK"].data[fiber_index : fiber_index + 1].astype(np.int32)
        rd = hdul[f"{B}_RESOLUTION"].data[fiber_index : fiber_index + 1]
        good = (iv[0] > 0) & (mk[0] == 0)
        coverage[B] = float(np.sum(good) / max(len(w), 1))
        wave_d[arm] = w
        flux_d[arm] = fl
        ivar_d[arm] = iv
        mask_d[arm] = mk
        res_d[arm] = rd

    sp = Spectra(
        bands=["b", "r", "z"],
        wave=wave_d,
        flux=flux_d,
        ivar=ivar_d,
        mask=mask_d,
        resolution_data=res_d,
    )
    sp_merged = coadd_cameras(sp)

    wave_out = sp_merged.wave["brz"]
    flux_out = sp_merged.flux["brz"][0]
    ivar_out = sp_merged.ivar["brz"][0]
    mask_out = sp_merged.mask["brz"][0].astype(np.int16)
    res_out = sp_merged.resolution_data["brz"][0]  # (n_diag, n_wave)

    return wave_out, flux_out, ivar_out, mask_out, res_out, coverage


def median_snr(flux, ivar, mask):
    good = (ivar > 0) & (mask == 0)
    if not np.any(good):
        return 0.0
    snr = flux[good] * np.sqrt(ivar[good])
    snr = snr[np.isfinite(snr)]
    return float(np.median(snr)) if snr.size else 0.0


def collect_rows(coadd_files, redrock_dir, allow_download, log):
    """Walk coadds, join with Redrock + SCORES, return per-target row dicts."""
    rows = []
    for ic, coadd_path in enumerate(coadd_files, 1):
        meta = parse_coadd_name(coadd_path)
        log(
            f"[{ic}/{len(coadd_files)}] {coadd_path.name}  "
            f"survey={meta['survey']} program={meta['program']} hpx={meta['healpix']}"
        )
        redrock_path = (
            redrock_dir
            / f"redrock-{meta['survey']}-{meta['program']}-{meta['healpix']}.fits"
        )
        if not redrock_path.exists():
            if not allow_download:
                log("    no redrock file and --skip-download set; skipping coadd")
                continue
            redrock_path = download_redrock(meta, redrock_dir, log)
            if redrock_path is None:
                continue

        kept = 0
        with fits.open(coadd_path) as hd_co, fits.open(redrock_path) as hd_rr:
            fmap = hd_co["FIBERMAP"].data
            scores = hd_co["SCORES"].data
            zinfo = hd_rr["REDSHIFTS"].data
            assert np.array_equal(fmap["TARGETID"], scores["TARGETID"]), (
                f"FIBERMAP/SCORES TARGETID misaligned in {coadd_path.name}"
            )
            rr_idx = {int(t): i for i, t in enumerate(zinfo["TARGETID"])}
            n_fibers = len(fmap)
            for fi in range(n_fibers):
                tid = int(fmap["TARGETID"][fi])
                if tid not in rr_idx:
                    continue
                cls = assign_class(fmap["DESI_TARGET"][fi])
                if cls is None:
                    continue
                fm = fmap[fi]
                sc = scores[fi]
                rr = zinfo[rr_idx[tid]]
                rows.append(
                    {
                        "class": cls,
                        "targetid": tid,
                        "desi_target": int(fm["DESI_TARGET"]),
                        "healpix": meta["healpix"],
                        "survey": meta["survey"],
                        "program": meta["program"],
                        "fiber_index": fi,
                        "coadd_file": str(coadd_path),
                        "ra": float(fm["TARGET_RA"]),
                        "dec": float(fm["TARGET_DEC"]),
                        "ebv": float(fm["EBV"]),
                        "coadd_fiberstatus": int(fm["COADD_FIBERSTATUS"]),
                        "flux_g": float(fm["FLUX_G"]),
                        "flux_r": float(fm["FLUX_R"]),
                        "flux_z": float(fm["FLUX_Z"]),
                        "flux_W1": float(fm["FLUX_W1"]),
                        "flux_W2": float(fm["FLUX_W2"]),
                        "flux_g_ivar": float(fm["FLUX_IVAR_G"]),
                        "flux_r_ivar": float(fm["FLUX_IVAR_R"]),
                        "flux_z_ivar": float(fm["FLUX_IVAR_Z"]),
                        "flux_W1_ivar": float(fm["FLUX_IVAR_W1"]),
                        "flux_W2_ivar": float(fm["FLUX_IVAR_W2"]),
                        "fiberflux_g": float(fm["FIBERFLUX_G"]),
                        "fiberflux_r": float(fm["FIBERFLUX_R"]),
                        "fiberflux_z": float(fm["FIBERFLUX_Z"]),
                        "z_redrock": float(rr["Z"]),
                        "zerr_redrock": float(rr["ZERR"]),
                        "zwarn": int(rr["ZWARN"]),
                        "spectype": str(rr["SPECTYPE"]).strip(),
                        "subtype": str(rr["SUBTYPE"]).strip(),
                        "deltachi2": float(rr["DELTACHI2"]),
                        "chi2": float(rr["CHI2"]),
                        "ncoeff": int(rr["NCOEFF"]),
                        "coeff": np.asarray(rr["COEFF"], dtype="f8"),
                        "tsnr2_lrg": float(sc["TSNR2_LRG"]),
                        "tsnr2_elg": float(sc["TSNR2_ELG"]),
                        "tsnr2_bgs": float(sc["TSNR2_BGS"]),
                        "tsnr2_qso": float(sc["TSNR2_QSO"]),
                    }
                )
                kept += 1
        log(f"    kept {kept} / {n_fibers} fibers (LRG+ELG+BGS, with redrock match)")
    return rows


def write_hdf5(rows, output, n_coadds, log, programs_processed=None):
    output.parent.mkdir(parents=True, exist_ok=True)

    programs_processed = programs_processed or set()
    has_bright = "bright" in programs_processed
    has_dark = "dark" in programs_processed

    by_class = {"BGS": [], "ELG": [], "LRG": []}
    for r in rows:
        by_class[r["class"]].append(r)
    # Stable, deterministic order
    for cls in by_class:
        by_class[cls].sort(key=lambda r: (r["healpix"], r["fiber_index"]))

    # Real BGS targets are only observed in main/bright. If no bright coadds
    # were processed, any objects collected into BGS are not real BGS — they
    # are dark-program targets whose DESI_TARGET happens to carry the BGS_ANY
    # bit alongside a primary class (most often QSO). Skip the group in that
    # case rather than write a misleading 5-object placeholder.
    bgs_skipped_reason = None
    if by_class["BGS"] and not has_bright:
        n_dropped = len(by_class["BGS"])
        log(
            f"  WARNING: dropping {n_dropped} BGS-tagged objects: no main/bright "
            f"coadds in input. These would be misleading — primary targeting is "
            f"not BGS. Re-run with bright coadds to extract real BGS."
        )
        bgs_skipped_reason = (
            f"no main/bright coadds in input; {n_dropped} BGS-tagged dark-program "
            "objects suppressed (real BGS observations require bright program)"
        )
        by_class["BGS"] = []

    with h5py.File(output, "w") as h:
        meta = h.create_group("metadata")
        meta.attrs["creation_date"] = datetime.now().isoformat()
        meta.attrs["script_version"] = SCRIPT_VERSION
        meta.attrs["script_name"] = "extract_desi_dr1_final.py"
        meta.attrs["desi_data_release"] = "DR1"
        meta.attrs["desi_reduction"] = "iron"
        meta.attrs["data_source"] = (
            "DESI DR1 public release (HEALPix coadds + Redrock + SCORES + FIBERMAP)"
        )
        meta.attrs["extraction_method"] = (
            "coadd_walk + desispec.coadd_cameras + redrock_join + fibermap_photometry + scores_quality"
        )
        meta.attrs["pipeline_version"] = "DESI_extraction_v4.0"
        meta.attrs["n_total_objects"] = sum(len(by_class[c]) for c in by_class)
        meta.attrs["n_coadds_processed"] = n_coadds
        meta.attrs["fastspecfit_populated"] = False
        meta.attrs["fastspecfit_note"] = (
            "Emission-line measurements from FastSpecFit are deferred to a "
            "follow-up update. The loader is at popfit/fastspecfit.py."
        )
        meta.attrs["class_priority"] = "|".join(GROUP_PRIORITY)
        # v3 additions
        meta.attrs["programs_processed"] = (
            "|".join(sorted(programs_processed)) or "unknown"
        )
        meta.attrs["has_bright_program"] = bool(has_bright)
        meta.attrs["has_dark_program"] = bool(has_dark)
        if bgs_skipped_reason:
            meta.attrs["bgs_group_skipped"] = True
            meta.attrs["bgs_group_skipped_reason"] = bgs_skipped_reason
        else:
            meta.attrs["bgs_group_skipped"] = False
        meta.attrs["v3_changelog"] = (
            "v3.0: (a) added per-row truth columns is_galaxy/is_qso/is_star "
            "(from Redrock SPECTYPE) and is_clean_galaxy/is_clean_qso/is_clean_star "
            "(zwarn==0 plus deltachi2 quality cuts); (b) added targeting-bit "
            "columns is_lrg_target/is_elg_target/is_qso_target/is_bgs_target "
            "(decoded from DESI_TARGET); (c) BGS group is suppressed when no "
            "main/bright coadds are present in the input; (d) fixed Redrock "
            "download URL pixgroup convention (healpix//100, was (healpix//100)*100); "
            "(e) removed misleading z_mock alias. Use z_redrock instead."
        )
        meta.attrs["truth_filter_recipe"] = (
            "Clean galaxy sample: cat['is_galaxy'] & cat['is_clean_galaxy']. "
            "Combine with targeting bit (e.g. cat['is_elg_target']) to restrict "
            "to a single selection function."
        )

        units = meta.create_group("units")
        units.attrs["wavelength"] = "Angstrom (vacuum, solar-system barycentric)"
        units.attrs["flux"] = "10^-17 erg/s/cm^2/Angstrom"
        units.attrs["ivar"] = "(10^17 erg/s/cm^2/Angstrom)^-2"
        units.attrs["mask"] = "DESI specmask bits (0 = good)"
        units.attrs["photometry"] = "nanomaggies (Legacy Survey/DR9)"
        units.attrs["photometry_ivar"] = "nanomaggies^-2"
        units.attrs["fiberflux"] = "nanomaggies (1 arcsec aperture)"
        units.attrs["ebv"] = "magnitudes (Schlegel-Finkbeiner-Davis 1998)"
        units.attrs["mw_transmission"] = (
            "dimensionless; multiplicative correction = 10**(-0.4 * A_lambda); "
            "Schlafly+11 recalibration of SFD"
        )
        units.attrs["coordinates"] = "degrees (ICRS J2000.0)"
        units.attrs["tsnr2"] = "template S/N^2 (DESI SCORES HDU)"

        for cls in ("BGS", "ELG", "LRG"):
            rs = by_class[cls]
            if not rs:
                log(f"  group {cls}: 0 objects (skipped)")
                continue
            n_obj = len(rs)
            log(f"  group {cls}: writing {n_obj} objects")
            g = h.create_group(cls)
            g.attrs["n_objects"] = n_obj
            g.attrs["galaxy_type"] = f"DESI_DR1_{cls}"
            g.attrs["selection"] = (
                f"DESI_TARGET bitmask match for {cls}, "
                f"with priority order {'>'.join(GROUP_PRIORITY)} when a fiber "
                "matches multiple classes"
            )

            cat = g.create_group("catalog")
            spec = g.create_group("spectra")
            phot = g.create_group("photometry")
            qual = g.create_group("quality")

            def col(key, dtype):
                return np.array([r[key] for r in rs], dtype=dtype)

            def col_str(key, length):
                return np.array(
                    [r[key].encode("utf-8") for r in rs], dtype=f"S{length}"
                )

            # ----- catalog -----
            cat.create_dataset("targetid", data=col("targetid", "i8"))
            cat.create_dataset("healpix", data=col("healpix", "i4"))
            cat.create_dataset("ra", data=col("ra", "f8"))
            cat.create_dataset("dec", data=col("dec", "f8"))
            cat.create_dataset("survey", data=col_str("survey", 10))
            cat.create_dataset("program", data=col_str("program", 10))
            cat.create_dataset("fiber_index", data=col("fiber_index", "i4"))
            cat.create_dataset("coadd_file", data=col_str("coadd_file", 256))
            z_rr = col("z_redrock", "f8")
            cat.create_dataset("z_redrock", data=z_rr)
            cat.create_dataset("zerr_redrock", data=col("zerr_redrock", "f8"))
            zwarn_arr = col("zwarn", "i8")
            cat.create_dataset("zwarn", data=zwarn_arr)
            spectype_bytes = col_str("spectype", 6)
            cat.create_dataset("spectype", data=spectype_bytes)
            cat.create_dataset("subtype", data=col_str("subtype", 20))
            dchi2_arr = col("deltachi2", "f8")
            cat.create_dataset("deltachi2", data=dchi2_arr)
            cat.create_dataset("chi2", data=col("chi2", "f8"))
            cat.create_dataset("ncoeff", data=col("ncoeff", "i4"))
            cat.create_dataset(
                "coeff", data=np.stack([r["coeff"] for r in rs]).astype("f8")
            )

            # ----- truth (Redrock SPECTYPE) and targeting-bit booleans -----
            is_galaxy = spectype_bytes == b"GALAXY"
            is_qso = spectype_bytes == b"QSO"
            is_star = spectype_bytes == b"STAR"
            zwarn0 = zwarn_arr == 0
            cat.create_dataset("is_galaxy", data=is_galaxy)
            cat.create_dataset("is_qso", data=is_qso)
            cat.create_dataset("is_star", data=is_star)
            cat.create_dataset(
                "is_clean_galaxy",
                data=is_galaxy & zwarn0 & (dchi2_arr > CLEAN_GALAXY_DCHI2),
            )
            cat.create_dataset(
                "is_clean_qso",
                data=is_qso & zwarn0 & (dchi2_arr > CLEAN_QSO_DCHI2),
            )
            cat.create_dataset("is_clean_star", data=is_star & zwarn0)
            dt_arr = col("desi_target", "i8")
            cat.create_dataset("desi_target", data=dt_arr)
            cat.create_dataset(
                "is_lrg_target", data=(dt_arr & DESI_BIT_LRG).astype(bool)
            )
            cat.create_dataset(
                "is_elg_target", data=(dt_arr & DESI_BIT_ELG).astype(bool)
            )
            cat.create_dataset(
                "is_qso_target", data=(dt_arr & DESI_BIT_QSO).astype(bool)
            )
            cat.create_dataset(
                "is_bgs_target", data=(dt_arr & DESI_BIT_BGS_ANY).astype(bool)
            )

            g.attrs["n_clean_galaxy"] = int(
                (is_galaxy & zwarn0 & (dchi2_arr > CLEAN_GALAXY_DCHI2)).sum()
            )
            g.attrs["n_qso_contamination"] = int(is_qso.sum())
            g.attrs["n_star_contamination"] = int(is_star.sum())
            g.attrs["n_zwarn_nonzero"] = int((~zwarn0).sum())

            # ----- photometry -----
            for band in ("g", "r", "z", "W1", "W2"):
                phot.create_dataset(f"flux_{band}", data=col(f"flux_{band}", "f4"))
                phot.create_dataset(
                    f"flux_{band}_ivar", data=col(f"flux_{band}_ivar", "f4")
                )
            # v1 schema also had flux_i; DESI photometry has no native i, keep NaN.
            phot.create_dataset("flux_i", data=np.full(n_obj, np.nan, dtype="f4"))
            for band in ("g", "r", "z"):
                phot.create_dataset(
                    f"fiberflux_{band}", data=col(f"fiberflux_{band}", "f4")
                )
            ebv_arr = col("ebv", "f4")
            for band, coeff in EXTINCTION_COEFFS.items():
                trans = (10 ** (-0.4 * coeff * ebv_arr)).astype("f4")
                phot.create_dataset(f"mw_transmission_{band}", data=trans)

            # ----- quality (some filled now, some after spectra are extracted) -----
            qual.create_dataset("extraction_success", shape=(n_obj,), dtype="bool")
            qual.create_dataset("snr_median", shape=(n_obj,), dtype="f4")
            qual.create_dataset("wavelength_coverage", shape=(n_obj,), dtype="f4")
            qual.create_dataset("n_pixels", shape=(n_obj,), dtype="i4")
            qual.create_dataset("spectral_resolution", shape=(n_obj,), dtype="f4")
            qual.create_dataset("zwarn", data=col("zwarn", "i8"))
            qual.create_dataset("deltachi2", data=col("deltachi2", "f4"))
            qual.create_dataset("tsnr2_lrg", data=col("tsnr2_lrg", "f4"))
            qual.create_dataset("tsnr2_elg", data=col("tsnr2_elg", "f4"))
            qual.create_dataset("tsnr2_bgs", data=col("tsnr2_bgs", "f4"))
            qual.create_dataset("tsnr2_qso", data=col("tsnr2_qso", "f4"))
            qual.create_dataset(
                "coadd_fiberstatus", data=col("coadd_fiberstatus", "i4")
            )
            qual.create_dataset("ebv", data=ebv_arr)

            # ----- spectra (open each unique coadd file once) -----
            by_coadd = defaultdict(list)
            for i, r in enumerate(rs):
                by_coadd[r["coadd_file"]].append((i, r))

            n_success = 0
            snrs = []
            for cf, items in by_coadd.items():
                with fits.open(cf, memmap=True) as hd:
                    for i, r in items:
                        sg = spec.create_group(f"obj_{i:05d}")
                        try:
                            wave, flx, iv, mk, res, coverage = extract_one_spectrum(
                                hd, r["fiber_index"]
                            )
                            snr = median_snr(flx, iv, mk)
                            sg.create_dataset(
                                "wavelength",
                                data=wave.astype("f4"),
                                compression="gzip",
                                compression_opts=4,
                            )
                            sg.create_dataset(
                                "flux",
                                data=flx.astype("f4"),
                                compression="gzip",
                                compression_opts=4,
                            )
                            sg.create_dataset(
                                "ivar",
                                data=iv.astype("f4"),
                                compression="gzip",
                                compression_opts=4,
                            )
                            sg.create_dataset(
                                "mask",
                                data=mk.astype("i2"),
                                compression="gzip",
                                compression_opts=4,
                            )
                            sg.create_dataset(
                                "resolution",
                                data=res.astype("f4"),
                                compression="gzip",
                                compression_opts=4,
                            )
                            sg.attrs["targetid"] = r["targetid"]
                            sg.attrs["extraction_success"] = True
                            sg.attrs["snr_median"] = snr
                            sg.attrs["n_pixels"] = len(wave)
                            sg.attrs["resolution_n_diag"] = res.shape[0]
                            for arm, cov in coverage.items():
                                sg.attrs[f"arm_{arm}_coverage"] = cov
                            qual["extraction_success"][i] = True
                            qual["snr_median"][i] = snr
                            qual["wavelength_coverage"][i] = float(
                                np.mean(list(coverage.values()))
                            )
                            qual["n_pixels"][i] = len(wave)
                            qual["spectral_resolution"][i] = DESI_TYPICAL_RESOLUTION
                            n_success += 1
                            snrs.append(snr)
                        except Exception as exc:
                            sg.attrs["extraction_success"] = False
                            sg.attrs["error"] = str(exc)
                            qual["extraction_success"][i] = False
                            qual["snr_median"][i] = 0.0
                            qual["wavelength_coverage"][i] = 0.0
                            qual["n_pixels"][i] = 0
                            qual["spectral_resolution"][i] = 0.0

            g.attrs["extraction_success_count"] = n_success
            g.attrs["extraction_success_rate"] = n_success / n_obj if n_obj else 0.0
            if snrs:
                g.attrs["median_snr"] = float(np.median(snrs))
            log(
                f"  group {cls}: {n_success}/{n_obj} successful; "
                f"median S/N = {np.median(snrs) if snrs else 0:.2f}"
            )


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--coadd-dir", type=Path, default=DESI_DR1_HEALPIX)
    p.add_argument("--redrock-dir", type=Path, default=Path("data/redrock_cache"))
    p.add_argument(
        "--output",
        type=Path,
        default=FINAL_DESI_EXTRACTION / "desi_dr1_extracted_spectra.h5",
    )
    p.add_argument(
        "--max-coadds",
        type=int,
        default=None,
        help="Process at most N coadds (for pilot runs)",
    )
    p.add_argument(
        "--skip-download",
        action="store_true",
        help="Do not download missing redrock files; skip those coadds",
    )
    p.add_argument(
        "--subsample-fraction",
        type=float,
        default=None,
        help="Keep this fraction of objects per class (e.g. 0.1 for 1/10th), "
        "sampled with a fixed random seed for reproducibility",
    )
    p.add_argument(
        "--subsample-seed",
        type=int,
        default=42,
        help="Random seed for --subsample-fraction (default: 42)",
    )
    args = p.parse_args(argv)

    def log(msg):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

    coadd_files = sorted(args.coadd_dir.glob("coadd-*.fits"))
    if args.max_coadds:
        coadd_files = coadd_files[: args.max_coadds]
    if not coadd_files:
        log(f"no coadd files found in {args.coadd_dir}")
        return 1

    programs_in_dir = {parse_coadd_name(p)["program"] for p in coadd_files}
    log(f"found {len(coadd_files)} coadd files")
    log(f"programs in input dir: {sorted(programs_in_dir)}")
    log(f"redrock dir: {args.redrock_dir}")
    log(f"output:     {args.output}")

    rows = collect_rows(
        coadd_files,
        args.redrock_dir,
        allow_download=not args.skip_download,
        log=log,
    )
    if not rows:
        log("no rows accumulated; aborting")
        return 1
    log(f"total rows kept: {len(rows)}")

    if args.subsample_fraction is not None:
        rng = np.random.default_rng(args.subsample_seed)
        by_cls = {}
        for r in rows:
            by_cls.setdefault(r["class"], []).append(r)
        rows = []
        for cls, cls_rows in by_cls.items():
            n_keep = max(1, round(len(cls_rows) * args.subsample_fraction))
            idx = rng.choice(len(cls_rows), size=n_keep, replace=False)
            idx.sort()
            rows.extend(cls_rows[i] for i in idx)
            log(f"  subsample {cls}: {len(cls_rows)} -> {n_keep}")
        log(f"total rows after subsample: {len(rows)}")

    # Derive programs_processed from rows that actually made it through
    # (excludes coadds skipped due to missing redrock files).
    programs_processed = {r["program"] for r in rows}
    log(f"programs actually contributing rows: {sorted(programs_processed)}")

    write_hdf5(
        rows,
        args.output,
        n_coadds=len(coadd_files),
        log=log,
        programs_processed=programs_processed,
    )
    size_mb = args.output.stat().st_size / 1e6
    log(f"DONE  {args.output}  ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
