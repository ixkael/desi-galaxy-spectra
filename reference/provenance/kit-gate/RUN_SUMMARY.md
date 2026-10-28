# Run summary: 2026-10-23-kit-gate

Written by the engineer role. Step 4 of `plans/2026-10-22-desi-more-data.md`: the `desi_kit/` implementation
(design `desi_kit/DESIGN.md`, approved 2026-10-23) and its five-part exact gate on pixel 21852, run from the
local NAS coadds with no download.

## Provenance
| Field | Value |
|---|---|
| Repo, commit | `pop-cosmos_DESI` @ `0e3f14d9` (dirty: yes; `desi_kit/` code new and uncommitted; `extract_desi_dr1_final.py`, `scripts/package_desi_zchunks.py`, `popfit/paths.py` unmodified, confirmed by `git diff`) |
| Command | repo root, `OMP/OPENBLAS/MKL/NUMBA_NUM_THREADS=1`, `G=<nas>/pop-cosmos_DESI_packages/kit_reference/kit_gate_21852`: `kit.py extract --pixel 21852 --local-flat <nas>/pop-cosmos_DESI_data_backup/desi_dr1_healpix --redrock-dir data/redrock_cache --shard-dir $G --staging <scratch>/staging --workers 1 --keep-temp`; `kit.py package --shard-dir $G --out $G/chunks_kit_res`; same with `--out $G/chunks_kit_nores --no-resolution`; `scripts/package_desi_zchunks.py --v4 <reference> --out $G/chunks_original --workers 4 --source-md5 20a4a9148dc13ccb0a9ca2a8ebb28b2d`; `kit.py manifest --target $G/chunks_kit_res --out $G/manifest.json`; `kit.py verify --target $G/chunks_kit_res --manifest $G/manifest.json --shard-dir $G`; `gate_checks_3to5.py --gate-dir $G`; `compare_gate_1to2.py` (independent). Full list: `<scratch>/2026-10-23-desi-kit/BUILD_NOTES.md` |
| Profile | engineer |
| Machine, environment | x86_64 workstation; fresh uv venv `<scratch>/2026-10-23-desi-kit/env`, python 3.10.20, built from `desi_kit/requirements.lock.txt` alone (byte-identical to the step-3 lock, sha256 b035c5b8…49cd; desispec 0.70.0, numpy 2.2.6, h5py 3.16.0) + pytest 9.1.1 outside the lock |
| Random seeds | none used |
| Inputs | `coadd-main-bright-21852.fits` (809,763,840 B), `coadd-main-dark-21852.fits` (1,185,212,160 B) in `<nas>/pop-cosmos_DESI_data_backup/desi_dr1_healpix/`; `data/redrock_cache/redrock-main-{bright,dark}-21852.fits`; sha256 of all four recorded in the shard and re-hashed by the verifier (match). Reference `<nas>/pop-cosmos_DESI_packages/kit_reference/desispec070_pixel21852.h5` (1,080,516,994 B, md5 20a4a9148dc13ccb0a9ca2a8ebb28b2d, confirmed) |
| Wall time | extraction 368.9 s + shard 69.1 s = 438.0 s per pixel pair, one core, peak memory 970 MB; packaging 40.7 s (with resolution), 8.2 s (without) |

## Model and configuration
- Stage: per-pixel extraction (`collect_rows`/`write_hdf5` imported from the extractor) → stacked per-pixel shard → redshift-chunk packaging (kit writer reusing the packager's `_read_block`, `EDGES`, `CLASSES`, `SELECTION`, `OBJ_ATTRS`, `RES_DESC`). No fit, no free parameters.
- Selection: none added by the kit (PHOTSYS recorded, not cut; BGS-from-dark kept). 2,831 class-tagged objects extracted; 2,365 clean galaxies packaged.

## Best-fit parameters
Not applicable.

## Diagnostics
| Diagnostic | Value | Reference | Pass? |
|---|---|---|---|
| 1. Kept temp v4-layout file vs reference (independent Opus verifier, no kit code) | 0 differences: 2,849 groups, 22,707 attrs, 14,335 datasets, 330,615,504 elements; dtype/shape/chunks/filters identical | exact; excluded only `metadata/creation_date` and directory part of `catalog/coadd_file` | **yes** |
| 2. Shard row i vs reference `obj_i` (independent) | bijection on (class, targetid, program), order preserved; 330.4M spectral elements, 24 `spectra_attrs`, 180 table columns: 0 differences | exact | **yes** |
| 3. Original packager on reference vs kit packager on shard | 15 files, 2,751 datasets, 0 differences | exact except declared provenance attrs + approved additions (itemised in `gate_checks_3to5.json`) | **yes** |
| 4. `--no-resolution` vs (3) | 15 files, 0 differences besides resolution datasets, `units@resolution`, `has_resolution`=False | exact | **yes** |
| 5. Manifest round-trip; injected 1-ULP in `BGS/spectra/flux[0,0]` | round-trip identical; `verify` fails and names file + dataset | detection | **yes** |
| 1-ULP sensitivity control in the independent comparator | flagged (reference ELG obj_00010, shard LRG row 5) | detection | yes |
| `catalog/photsys` (new) | all 'S': BGS 867, ELG 1,337, LRG 627 | no reference | recorded |
| pytest `desi_kit/tests` | 21 passed, 0 failed | all pass | **yes** |

## Outputs
| Path | What it is |
|---|---|
| `desi_kit/{kit.py,README.md,requirements.lock.txt,desikit/*.py,tests/*.py}` | the kit (uncommitted) |
| `<nas>/pop-cosmos_DESI_packages/kit_reference/kit_gate_21852/` | shard `healpix_21852.h5` (999,410,870 B), kept temp `v4layout_21852.h5`, `chunks_kit_res/`, `chunks_kit_nores/`, `chunks_original/`, `manifest.json`, `extract_log.jsonl` |
| `gate_measurements.json` | measured constants for the step-5 projection: 438.0 s per pixel pair; 355,015 B/object with resolution, 57,715 B/object without (2,365 clean objects) |
| `compare_gate_1to2.{py,json}`, `VERIFY_GATE_1to2.md` | independent verifier, checks 1–2 |
| `gate_checks_3to5.{py,json}` | checks 3–5 (written by the implementer) |
| `CODE_REVIEW.md` | Opus code review: 2 blockers for step 6, 11 should-fix, 8 nits |

## Checks and status
- Gates passed: 1, 2, 3, 4, 5, pytest. Gates failed: none.
- Change log vs DESIGN.md (implementer's declared deviations): gate-only `extract --keep-temp`; extra module `desikit/dryrun.py`; `--workers` parallelises pixel pairs, packaging serial; `verify --shard-dir` optional (shard-dependent checks skipped without it); `--no-resolution` also omits `units@resolution`; `--fetch` rewrites `catalog/coadd_file` to the permanent raw dir; per-object extraction failures (swallowed by the extractor) made a hard error by the kit; extra root attr `python_version` in the shard; extra log `extract_log.jsonl`.
- Blockers for step 6 (from `CODE_REVIEW.md`, not for the gate or the dry run): (a) raw-file move to the NAS is copy-then-delete with no re-hash at the destination, and a crashed move is never repaired because the pixel is then skipped (`shard.py:298`, `335-343`); (b) chunk files built from many shards copy group attrs (`n_objects`, `median_snr`, …) and `source_metadata` from the first shard only (`package.py:161,188`).
- Known gap in the gate script: `gate_checks_3to5.py` would pass on two empty directories (the recorded run compared 15 files).
- Needs my decision: how multi-pixel chunks should carry per-shard group attrs and metadata (blocker b); approval to fix blocker (a) and the should-fix items listed in `CODE_REVIEW.md` before step 6.

## Not interpreted
No physical conclusion is drawn here.
