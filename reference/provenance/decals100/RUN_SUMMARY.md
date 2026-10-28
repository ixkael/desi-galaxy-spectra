# Run summary: 2026-10-23-desi-decals100

Written by the engineer role. Facts and diagnostics only; no interpretation. Fetch, extraction,
per-pixel shards and redshift-chunk packaging (with resolution matrices) for the 100 DECaLS-footprint
pixels of `desi_kit/pixel_lists/dr1_decals_random100.txt`. Status: DONE.

## Provenance
| Field | Value |
|---|---|
| Repo, commit | `pop-cosmos_DESI` @ `aa237d44` (dirty: yes in the gate summary; dirty state at this run not re-checked), kit `desi_kit` 0.2.0 |
| Command (extraction) | `kit.py extract --pixels desi_kit/pixel_lists/dr1_decals_random100.txt --fetch --raw-root <nas>/desi_dr1/healpix_main --shard-dir <nas>/pop-cosmos_DESI_packages/v4_decals_shards --staging <scratch>/2026-10-23-desi-decals100/staging --workers 4` (via `python3 -m popfit.runlog launch --id desi-decals100 ...`, driver `launch.sh`) |
| Command (packaging) | `kit.py package --shard-dir <shards> --out <package dir>`; `kit.py manifest --target <package dir> --out <package dir>/manifest.json`; `kit.py verify --target <package dir> --manifest <package dir>/manifest.json --shard-dir <shards>`; `md5sum *.h5 manifest.json > MD5SUMS.txt` (driver `package.sh`) |
| Profile | engineer |
| Machine, environment | x86_64 workstation, `<scratch>/2026-10-23-desi-kit/env` (lockfile sha256 `b035c5b8...97e49cd`, in manifest) |
| Random seeds | 20261023 (pixel draw, recorded in the pixel list header) |
| Inputs | 100 pixels, bright and dark programmes: raw coadd and Redrock files fetched by HTTPS, sha256-verified, kept in `<nas>/desi_dr1/healpix_main` (82,678,678,192 B) |
| Wall time | extraction 3:10:49 (09:50:10 to 13:00:59); packaging 41:02.51 (13:16:40 to 13:57:42); manifest 13:57:42 to 14:03:10; verify 14:03:10 to 14:08:52; md5 14:08:52 to 14:10:02 |

Packaging `/usr/bin/time -v`: user 1589.60 s, system 41.08 s, 66% CPU, peak RSS 633,580 kB, exit 0.

## Model and configuration
- Stage: per-pixel extraction, per-pixel shard, redshift-chunk packaging (kit 0.2.0), 4 extraction workers, resolution included. No fit, no free parameters.
- Selection in the chunks: `is_galaxy & is_clean_galaxy`.

| # | Cut expression | Rows before | Rows after | Removed (%) | Reference |
|---|---|---|---|---|---|
| 1 | class-tagged objects in the 100 shards to `is_galaxy & is_clean_galaxy` in the 15 chunks | 117,359 | 97,553 | 16.88 | `extract_log.jsonl` (sum of `n_objects`); manifest |

## Diagnostics
`kit.py verify` (log `package.log`, 14:03:10 to 14:08:52): VERIFY PASSED.

| Check | Result |
|---|---|
| manifest recomputes identically | PASS |
| root n_* equal actual counts | PASS |
| (targetid, program) unique; no shard row repeated | PASS |
| z inside [z_lo, z_hi) | PASS |
| wavelength identical across files | PASS |
| unit keys present | PASS |
| spectra_attrs targetid == catalog targetid | PASS |
| has_resolution consistent with datasets; resolution dtype | PASS |
| per-shard table: one row per source shard, healpix = source_shards, chunk-level attrs = common value | PASS |
| every clean shard row in exactly one chunk; z equals shard | PASS |

Counts and timings (from `extract_log.jsonl`, manifest, `package.log`):

| Quantity | Value |
|---|---|
| Shards written, failures | 100, none (100 lines in `extract_log.jsonl`; no failed pixel recorded; `extract.log` has no line matching fail or error) |
| Objects per shard | sum 117,359; min 189; median 906; max 3,086 |
| Shard bytes (sum of `shard_bytes`) | 41,327,409,324 (directory with log: 41,327,512,845 B) |
| Per-pixel time (`seconds_total`) | sum 28,392.9 s over 4 workers; min 33.6 s; median 205.8 s; max 1,073.5 s (extract part sum 24,692.8 s, shard part sum 3,700.1 s) |
| Per-pixel peak RSS | max 1,358.4 MB |
| Clean objects in the 15 chunks | BGS 45,698; ELG 28,425; LRG 23,430; total 97,553 |
| Projected totals (dry run) | BGS 45,698; ELG 28,425; LRG 23,430; total 97,553 |
| Chunk files | 15, sum of sizes 34,528,108,899 B |
| Physical assertions | z within each chunk's [z_lo, z_hi) and equal to the shard value (PASS above); no fit, so no chi-square, residual, pull or convergence diagnostic: not produced: no fit |

## Outputs
| Path | What it is |
|---|---|
| `<nas>/pop-cosmos_DESI_packages/v4_decals_zchunks/` | 15 chunk files, `manifest.json`, `MD5SUMS.txt`, `README.md`, this summary |
| `<nas>/pop-cosmos_DESI_packages/v4_decals_shards/` | 100 shards `healpix_<pixel>.h5` and `extract_log.jsonl` |
| `<nas>/desi_dr1/healpix_main/` | raw coadd, Redrock, checksum files (82,678,678,192 B) |
| `<scratch>/2026-10-23-desi-decals100/` | `launch.sh`, `package.sh`, `extract.log`, `package.log`, `package_time.txt`, `summarise.py`, `write_docs.py` |
| `desi_kit/reference/2026-10-23-decals100/{RUN_SUMMARY.md,run_summary.json}` | copy of this summary |

## Checks and status
- Gates passed: kit verify (10 checks), shard-count guard (100 shards before packaging). Gates failed: none.
- Change log: none.
- Blockers / open items: independent verification (`VERIFY.md`) is written separately and is not part of this summary.
- Needs my decision: none from this run.

## Not interpreted
No physical conclusion is drawn here.
