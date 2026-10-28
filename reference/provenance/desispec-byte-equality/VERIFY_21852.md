# Verification: desispec 0.70.0 re-extraction vs v4 reference, HEALPix 21852

Date 2026-10-23. Independent read-only check. Script `compare_21852.py` (full results in
`compare_21852.json`), helpers `summarise.py`, `peek_diffs.py`, `peek_layout.py`, `peek_v4.py`, all in this directory.

- Reference: `<nas>/pop-cosmos_DESI_data_backup/final_desi_extraction/desi_dr1_extracted_spectra_v4.h5`
- New: `<scratch>/2026-10-23-desi-step3/v070_21852.h5` (desispec 0.70.0, numpy 2.2.6)

## Verdict

**The spectra are byte-identical.** For all 2,831 matched objects, every one of flux, ivar, mask,
resolution and wavelength is identical bit for bit, and so are all per-object attributes.
The tables (catalogue, photometry, quality) match except for two things, neither coming from desispec:
1. `catalog/coadd_file` holds the input path, which differs because the coadds were read from a different directory (same file names).
2. `photometry/mw_transmission_{g,r,z,W1,W2}`: 132 of 14,155 values (0.93%) differ by exactly 1 float32 ULP (unit in the last place, |Δ| = 5.96e-8, relative about 6.6e-8). The input `quality/ebv` is bit-identical. The extractor computes `(10 ** (-0.4*coeff*ebv_f4)).astype("f4")` on float32 inputs (extract_desi_dr1_final.py:493), so the differences come from numpy's float32 power function (numpy version or CPU SIMD code path), not from desispec. At this size they have no physical consequence.

## How objects were mapped

- In v4, the rows with healpix==21852 are the first rows in every class (rows 0..n-1, contiguous).
- Checked rather than assumed: `spectra/obj_NNNNN` corresponds to catalogue row NNNNN. Its `targetid` attribute equals `catalog/targetid[NNNNN]` for every object in both files (0 mismatches).
- Objects were matched on the key (class, targetid, program). The keys are unique within each class, and the row order is the same in both files.

| class | v4 rows (hp 21852) | new rows | matched | only v4 | only new | programmes |
|---|---|---|---|---|---|---|
| BGS | 867 | 867 | 867 | 0 | 0 | bright 867 |
| ELG | 1337 | 1337 | 1337 | 0 | 0 | dark 1337 |
| LRG | 627 | 627 | 627 | 0 | 0 | bright 64, dark 563 |
| total | 2831 | 2831 | 2831 | 0 | 0 | |

## Spectral datasets (all 2,831 objects)

The byte test compares the arrays' raw bytes (`np.array_equal` on uint8 views). As a negative control, it does detect a 1-ULP change.

| dataset | dtype | shape | objects compared | dtype / shape mismatches | objects not byte-identical | elements differing / total | NaN in v4 / new | max abs Δ |
|---|---|---|---|---|---|---|---|---|
| flux | float32 | (7781,) | 2831 | 0 / 0 | 0 | 0 / 22,028,011 | 0 / 0 | 0 |
| ivar | float32 | (7781,) | 2831 | 0 / 0 | 0 | 0 / 22,028,011 | 0 / 0 | 0 |
| mask | int16 | (7781,) | 2831 | 0 / 0 | 0 | 0 / 22,028,011 | n/a | 0 |
| resolution | float32 | (11, 7781) | 2831 | 0 / 0 | 0 | 0 / 242,308,121 | 0 / 0 | 0 |
| wavelength | float32 | (7781,) | 2831 | 0 / 0 | 0 | 0 / 22,028,011 | 0 / 0 | 0 |

- No element differs anywhere, so there is nothing to break down by wavelength region. That includes the arm overlaps at 5600–5950 Å and 7400–7700 Å.
- The storage settings (gzip, compression level, chunking, shuffle) are also the same for every dataset.
- The equality refers to the decoded array values. The two files' raw on-disk bytes were not compared.

## Per-object attributes (all matched objects)

The eight attributes are arm_B/R/Z_coverage, extraction_success, n_pixels, resolution_n_diag, snr_median and targetid. They are identical in value and dtype for all 2,831 objects (0 differences).

## Table columns (60 per class, 180 in total)

162 columns are byte-identical. That includes z_redrock, coeff, chi2, all fluxes and ivars, ebv, tsnr2_*, snr_median, flux_i (NaN in both, same bits), and the remaining columns. The other 18 differ:

| column | BGS rows differing | ELG | LRG | nature |
|---|---|---|---|---|
| catalog/coadd_file | 867 | 1337 | 627 | path prefix only: `pop-cosmos-KIDS_DESI/desi_dr1_healpix/` → `<scratch>/2026-10-23-desi-step3/coadds21852/`, same file name |
| photometry/mw_transmission_g | 5 | 16 | 6 | 1 ULP, both signs |
| photometry/mw_transmission_r | 9 | 16 | 2 | 1 ULP |
| photometry/mw_transmission_z | 8 | 17 | 2 | 1 ULP |
| photometry/mw_transmission_W1 | 10 | 9 | 6 | 1 ULP |
| photometry/mw_transmission_W2 | 7 | 16 | 3 | 1 ULP |

The maximum ULP difference is 1, the maximum |Δ| is 5.96e-8 and the maximum relative Δ is about 6.6e-8.

## Group and file metadata

- `metadata` attributes: only creation_date, n_coadds_processed (44 → 2) and n_total_objects (53226 → 2831) differ, and these are expected to differ. Every other attribute is identical, including pipeline_version, script_version, desi_reduction and the changelog. All `metadata/units` attributes are identical.
- Class-group attributes (n_objects, median_snr, n_clean_galaxy, n_*_contamination, n_zwarn_nonzero, extraction_success_count) differ. This is expected: in v4 they summarise all 44 coadds, in the new file only this pixel. They are not a per-object comparison. galaxy_type, selection and extraction_success_rate are identical.
