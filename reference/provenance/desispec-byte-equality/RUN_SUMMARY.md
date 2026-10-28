# Run summary: 2026-10-23-desispec-byte-equality

Written by the engineer role. Step 3 of `plans/2026-10-22-desi-more-data.md`: install desispec
(pinned) and re-extract pixel 21852 (bright + dark), requiring byte equality with v4.

## Provenance
| Field | Value |
|---|---|
| Repo, commit | `pop-cosmos_DESI` @ `0e3f14d9` (dirty: yes; `extract_desi_dr1_final.py` itself unmodified at HEAD) |
| Command | `env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMBA_NUM_THREADS=1 <scratch>/2026-10-23-desi-step3/env070/bin/python extract_desi_dr1_final.py --coadd-dir <scratch>/2026-10-23-desi-step3/coadds21852 --redrock-dir data/redrock_cache --skip-download --output <scratch>/2026-10-23-desi-step3/v070_21852.h5` (repo root; coadd dir holds symlinks to the two NAS coadds) |
| Profile | engineer |
| Machine, environment | x86_64 workstation; uv venv, python 3.10, desispec 0.70.0 (PyPI), numpy 2.2.6, scipy 1.15.3, desiutil 4.0.1; full freeze `requirements-desispec-0.70.0.lock.txt` |
| Random seeds | none used |
| Inputs | `<nas>/pop-cosmos_DESI_data_backup/desi_dr1_healpix/coadd-main-bright-21852.fits` (809,763,840 B), `coadd-main-dark-21852.fits` (1,185,212,160 B); `data/redrock_cache/redrock-main-{bright,dark}-21852.fits`; reference v4.h5 (md5 05b36b97…01f1) |
| Wall time | 387 s (6 min 27 s), one core, peak memory 0.99 GB |

## Model and configuration
- Stage: extraction (`coadd_cameras` merge of b, r, z arms per fibre); no fit, no free parameters.
- Version choice: v4 records no desispec version. Iron itself used desispec 0.56.4 (coadd header
  DEPVER); PyPI offers only 0.70.0 (uploaded 2025-12-04, the current release when v4 was built on
  2026-06-09). 0.70.0 was tried first; the 0.56.4 fallback was not needed.

## Best-fit parameters
Not applicable.

## Diagnostics
Independent comparison by an Opus verifier: `VERIFY_21852.md`, `compare_21852.py`, `compare_21852.json` (this directory).

| Diagnostic | Value | Reference | Pass? |
|---|---|---|---|
| Objects matched on (class, targetid, program) | 2,831 / 2,831 (BGS 867, ELG 1,337, LRG 627), 0 unmatched, same order | v4 rows with healpix 21852 | yes |
| zpix tagged rows in pixel 21852 (step 1 cache) | 2,831 | extractor rows kept 2,831 | yes |
| flux, ivar, mask, resolution, wavelength: raw bytes | 0 differing elements (22.0M per 1-D dataset, 242M resolution); dtype, shape, compression identical; no NaN | byte equality | **yes** |
| Per-object attributes (8) | all identical | equality | yes |
| `photometry/mw_transmission_*` | 132 of 14,155 values differ by exactly 1 float32 ULP (5.96e-8); input `ebv` identical | equality | no (not a spectral dataset; float32 `10**x`, extractor l.493, numpy version) |
| `catalog/coadd_file` | directory part of the path differs; file names identical | — | expected |
| A 1-ULP control injected by the verifier | detected | — | test is sensitive |

## Outputs
| Path | What it is |
|---|---|
| `<nas>/pop-cosmos_DESI_packages/kit_reference/desispec070_pixel21852.h5` | reference extraction for the step-4 kit gate, 1,080,516,994 B, md5 20a4a9148dc13ccb0a9ca2a8ebb28b2d |
| `requirements-desispec-0.70.0.lock.txt` | `uv pip freeze` of the environment (candidate kit lockfile) |
| `VERIFY_21852.md`, `compare_21852.py`, `compare_21852.json` | verifier report, script, full results |

## Checks and status
- Gates passed: step-3 byte equality of the spectral datasets on pixel 21852 (bright + dark).
- Gate not met as literally "every column": `mw_transmission_*` 1-ULP float32 differences (0.93% of values), from numpy, not desispec.
- Timing projection [estimate from this run]: 0.137 s per object single core; the drawn 100 pixels hold 117,359 class-tagged objects (step-1 cache), i.e. ~4.5 h on one core, before NAS read time.
- Change log: none (extractor unmodified).
- Needs my decision: accept the 1-ULP `mw_transmission` difference, or pin numpy to the version used for v4 (unknown) — the kit's own gate (kit vs this reference) is unaffected because both use the pinned lockfile.

## Not interpreted
No physical conclusion is drawn here.
