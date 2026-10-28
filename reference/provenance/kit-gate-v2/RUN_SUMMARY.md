# Run summary: 2026-10-23-kit-gate-v2

Written by the engineer role. Rerun of the five-part exact gate on pixel 21852 with the fixed kit
(`desi_kit` 0.1.0 to 0.2.0; fixes in `desi_kit/reference/2026-10-23-kit-gate/FIX_LOG.md`, review in
`FIX_REVIEW.md` there). Same inputs, same checks, fresh output directory. Mirrors the v1 summary
`desi_kit/reference/2026-10-23-kit-gate/RUN_SUMMARY.md`.

## Provenance
| Field | Value |
|---|---|
| Repo, commit | `pop-cosmos_DESI` @ `aa237d44` (dirty: yes; kit 0.2.0 fixes uncommitted; diff `<scratch>/2026-10-23-desi-fixes/fixes.diff`) |
| Command | Driver `<scratch>/2026-10-23-desi-fixes/run_gate_v2.sh` (repo root, all thread-count variables = 1, `G=<nas>/pop-cosmos_DESI_packages/kit_reference/kit_gate_21852_v2`, `REF=.../kit_reference/desispec070_pixel21852.h5`): `kit.py extract --pixel 21852 --local-flat <nas>/pop-cosmos_DESI_data_backup/desi_dr1_healpix --redrock-dir data/redrock_cache --shard-dir $G --staging <scratch>/gate_v2_staging --workers 1 --keep-temp` (under `/usr/bin/time -v`); `kit.py package --shard-dir $G --out $G/chunks_kit_res`; same with `--out $G/chunks_kit_nores --no-resolution`; `scripts/package_desi_zchunks.py --v4 $REF --out $G/chunks_original --workers 4 --source-md5 20a4a9148dc13ccb0a9ca2a8ebb28b2d`; `kit.py manifest --target $G/chunks_kit_res --out $G/manifest.json`; `kit.py verify --target $G/chunks_kit_res --manifest $G/manifest.json --shard-dir $G`; `python -I compare_gate_1to2.py $REF $G/v4layout_21852.h5 $G/healpix_21852.h5 $G/compare_gate_1to2.json` (the unchanged independent comparator); `gate_checks_3to5.py --gate-dir $G --scratch <scratch>/gate_v2_scratch`; `python -m pytest desi_kit/tests -q` |
| Profile | engineer |
| Machine, environment | x86_64 workstation; uv venv `<scratch>/2026-10-23-desi-kit/env` (same as the v1 gate: python 3.10.20, `desi_kit/requirements.lock.txt`, pytest outside the lock) |
| Random seeds | none used |
| Inputs | Same four files as v1: `coadd-main-bright-21852.fits` (809,763,840 B), `coadd-main-dark-21852.fits` (1,185,212,160 B), `data/redrock_cache/redrock-main-bright-21852.fits` (1,474,560 B), `redrock-main-dark-21852.fits` (2,234,880 B); sizes re-read today. Reference `desispec070_pixel21852.h5` (1,080,516,994 B); its md5 was recomputed by the comparator and equals the expected `20a4a9148dc13ccb0a9ca2a8ebb28b2d` (`ref_md5_ok` = true in `compare_gate_1to2.json`). The shard's recorded input sha256 values are in `compare_gate_1to2.json` (`root_attrs.input_sha256`). |
| Wall time | See table below. Extraction (one core) 432.85 s elapsed by `/usr/bin/time`; the kit's own log gives 363.23 s extraction + 67.91 s shard = 431.14 s. Whole driver 09:37:39 to 09:48:48 (669 s). Peak memory 996,308 kB (about 973 MB). |

Wall time per stage (from the `===` timestamps in `gate_v2.log`; each is the interval to the next line):

| Stage | Start | Seconds |
|---|---|---|
| extract (with shard, kept temp file) | 09:37:39 | 433 |
| package, with resolution | 09:44:52 | 45 |
| package, `--no-resolution` | 09:45:37 | 10 |
| package, original packager on reference | 09:45:47 | 38 |
| manifest | 09:46:25 | 7 |
| verify | 09:46:32 | 9 |
| checks 1-2 (independent comparator) | 09:46:41 | 62 |
| checks 3-5 | 09:47:43 | 46 (script reports 45.9 s) |
| pytest | 09:48:29 | 19 |
| end (ALL_DONE) | 09:48:48 | - |

`/usr/bin/time -v` for extraction: user 425.52 s, system 4.11 s, 99% CPU, exit status 0.

## Model and configuration
- Stage: per-pixel extraction, stacked per-pixel shard, redshift-chunk packaging (kit 0.2.0). No fit, no free parameters.
- Selection: none added by the kit. 2,831 class-tagged objects extracted (BGS 867, ELG 1,337, LRG 627), as in v1. The number of clean galaxies packaged was not re-counted in this summary: not produced (not a field in the gate JSONs; v1 recorded 2,365).

## Best-fit parameters
Not applicable.

## Diagnostics
| Diagnostic | Value (read from the JSONs) | Reference | Pass? |
|---|---|---|---|
| 1. Kept temp v4-layout file vs reference (independent comparator, unchanged) | 0 differences: 2,849 groups, 22,707 attributes, 14,335 datasets, 330,615,504 elements compared; storage (dtype, chunks, filters) identical | exact; excluded only `metadata/creation_date` and the directory part of `catalog/coadd_file` (basenames equal) | **yes** |
| 2. Shard row i vs reference object i (independent) | Bijection with identity order on (class, targetid, program) for BGS 867, ELG 1,337, LRG 627; `targetid` mismatches 0. Spectra: flux, ivar, mask, wavelength 22,028,011 elements each, resolution 242,308,121, all 2,831 rows each, 0 differing. 24 `spectra_attrs`, 0 differences. 180 reference table columns, 0 differences. Class attrs (10 per class) and metadata attrs (19 + 11 units) 0 differences. Total 14,359 datasets, 330,638,152 elements compared, 0 differing | exact | **yes** |
| 3. Original packager on reference vs kit packager on shard | 15 files compared (15 non-empty chunks on each side, no file-set problems); 2,751 datasets byte-equal, 980 attributes, 3,062 objects compared; 0 differences | exact except declared provenance items and approved additions (itemised in `gate_checks_3to5.json`) | **yes** |
| 3, new: per-shard table `/source_shard_table` assertion | Table excluded from the A-vs-B comparison but its contents asserted in every file: `healpix` has exactly 1 row equal to 21852; each `source_metadata` and per-class column has 1 row; each value equals the matching shard attribute (strings compared by value); the `__valid` flag is true exactly when the shard has the attribute; classes in table match classes in shard. `additions_assertions_failed` = [] | assertions all hold | **yes** |
| 4. `--no-resolution` vs with-resolution | 15 files, 4,319 datasets, 1,025 attributes compared, 0 differences besides the declared items (resolution datasets, `units@resolution`, `has_resolution` True vs False, timestamp); `has_resolution_assertions_failed` = [] | exact | **yes** |
| 5. Manifest round-trip; injected 1-ULP in `BGS/spectra/flux[0,0]` of `desi_dr1_clean_galaxies_z0.10-0.20.h5` | Manifest of 15 files recomputes identically; pristine `verify` passes. Injected change -17.756799697875977 to -17.756797790527344: `verify` fails and names the file and the dataset (`BGS/spectra/flux md5 differs`) | detection | **yes** |
| `kit.py verify` on pristine chunks | VERIFY PASSED; PASS lines: manifest recomputes identically; root n_* equal actual counts; (targetid, program) unique, no shard row repeated; z inside [z_lo, z_hi); wavelength identical across files; unit keys present; spectra_attrs targetid == catalog targetid; has_resolution consistent with datasets, resolution dtype; per-shard table (one row per source shard, healpix = source_shards, chunk-level attrs = common value); every clean shard row in exactly one chunk, z equals shard | all pass | **yes** |
| 1-ULP sensitivity control in the comparator | Flagged on reference ELG obj_00010 flux (delta 1.49e-8, 1 element) and shard LRG row 5 flux (1 element); unmodified files compared equal | detection | yes |
| `catalog/photsys` (not in reference) | all 'S': BGS 867, ELG 1,337, LRG 627 (same as v1) | no reference | recorded |
| pytest `desi_kit/tests` | 51 passed, 0 failed (18.75 s) | all pass | **yes** |

Note on test count: `FIX_LOG.md` quotes 50 passed (`pytest.txt`); the gate-time run shows 51. The extra test's origin is not traced here.

## Comparison with the v1 gate
| Quantity | v1 (kit 0.1.0) | v2 (kit 0.2.0) |
|---|---|---|
| Shard size `healpix_21852.h5` | 999,410,870 B | 999,410,870 B (from `ls -l`; identical size) |
| Extraction + shard time, one core | 438.0 s | 431.14 s (kit log); 432.85 s elapsed by `/usr/bin/time` |
| Peak memory | 970 MB | 972.96 MB (kit log); 996,308 kB by `/usr/bin/time` |
| Check 1 elements | 330,615,504 | 330,615,504 |
| Check 3 files / datasets | 15 / 2,751 | 15 / 2,751 |
| pytest | 21 passed | 51 passed |
Per-pixel time differs by 6.9 s (1.6%) from v1; this is one run on each side, with no repeat to give a spread, so no statement on significance is made.

## Outputs
| Path | What it is |
|---|---|
| `<nas>/pop-cosmos_DESI_packages/kit_reference/kit_gate_21852_v2/` | `healpix_21852.h5` (999,410,870 B), kept temp `v4layout_21852.h5`, `chunks_kit_res/`, `chunks_kit_nores/`, `chunks_original/`, `manifest.json`, `extract_log.jsonl`, `compare_gate_1to2.json`, `gate_checks_3to5.json` |
| `<scratch>/2026-10-23-desi-fixes/gate_v2.log`, `gate_v2_time.txt`, `run_gate_v2.sh`, `fixes.diff` | full log, `/usr/bin/time` output, driver, diff of the 0.2.0 fixes |
| `desi_kit/reference/2026-10-23-kit-gate-v2/{RUN_SUMMARY.md,run_summary.json}` | this summary |
Copies of `compare_gate_1to2.json` and `gate_checks_3to5.json` into the repo reference folder: not done (only the two summary files were to be written).

## Checks and status
- Gates passed: 1, 2, 3 (including the new table assertion), 4, 5, verify, pytest. Gates failed: none.
- Change log vs the v1 gate:
  - The per-shard table `/source_shard_table` is now written by the packager (approved by PI ruling 2026-10-23, item B2). Check 3 excludes it from the A-vs-B comparison and instead asserts its contents.
  - String datasets (object-dtype) are compared by value, not by stored pointer, in `gate_checks_3to5.py`.
  - Empty or mismatched file sets now fail checks 3 and 4: the script requires the compared file count to equal the number of non-empty chunks and to be above zero (v1 would have passed on two empty directories). Here 15 = 15.
  - `kit.py verify` fails if shard checks are skipped unless `--no-shards` is given; the driver passes `--shard-dir`.
  - Page-cache drop: `drop_page_cache()` (fsync plus `posix_fadvise(DONTNEED)`) was added to `desikit/fetch.py` so the destination re-hash after a raw-file move cannot read this machine's cache. This gate used local coadds, no download and no raw-file move (`raw_files_moved` = null), so that code path was not exercised here; it is covered by a unit test only.
  - Other 0.2.0 fixes (failed-pixel handling and logging, `--fetch` guard, programme guard, HEAD retries, checksum-failure cleanup) are listed in `FIX_LOG.md`; none is exercised by this single-pixel local run beyond the passing tests.
  - The comparator for checks 1-2 is the unchanged file `compare_gate_1to2.py`.
- Blockers / open items: none arising from this run. Chunk files written by 0.1.0 must be repackaged (they lack the table).
- Needs my decision: none from this run.

## Not interpreted
No physical conclusion is drawn here.
