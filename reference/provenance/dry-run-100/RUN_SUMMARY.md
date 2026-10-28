# Run summary: 2026-10-23-dry-run-100

Written by the engineer role. Step 5 of `plans/2026-10-22-desi-more-data.md`, per the DESIGN.md addendum:
`extract --dry-run` on the seeded 100-pixel DECaLS-region list. HTTP HEAD requests only; no file was downloaded.

## Provenance
| Field | Value |
|---|---|
| Repo, commit | `pop-cosmos_DESI` @ `0e3f14d9` (dirty: yes; `desi_kit/` uncommitted, as gated in `../2026-10-23-kit-gate/`) |
| Command | repo root, thread vars = 1: `<scratch>/2026-10-23-desi-kit/env/bin/python desi_kit/kit.py extract --dry-run --pixels desi_kit/pixel_lists/dr1_decals_random100.txt --raw-root <nas>/desi_dr1 --shard-dir <nas>/pop-cosmos_DESI_packages --staging <scratch>/2026-10-23-desi-kit/staging --workers 4` |
| Profile | engineer |
| Machine, environment | x86_64 workstation; `<scratch>/2026-10-23-desi-kit/env` (lockfile env of the gate) |
| Random seeds | none here (pixel list drawn with seed 20261023 in step 1) |
| Inputs | `desi_kit/pixel_lists/dr1_decals_random100.txt` (100 pixels), `pixel_photsys_table.csv`, `counts_class_chunk.csv` (step 1); `../2026-10-23-kit-gate/gate_measurements.json`; HEAD on `https://data.desi.lbl.gov/public/dr1/spectro/redux/iron/healpix/main/{bright,dark}/<pix//100>/<pix>/` |
| Wall time | 110 s (600 HEAD requests), finished 2026-10-23 08:52 BST |

## Model and configuration
- Stage: dry run of the per-pixel fetch → extract → shard → package pipeline. No fit, no selection applied.
- Projection rules (DESIGN.md addendum): counts from the step-1 table; package size = clean galaxies × gate bytes per object; time = gate seconds per pixel pair × pixels.

## Best-fit parameters
Not applicable.

## Diagnostics
| Quantity | Value | Basis |
|---|---|---|
| Files found | 600 / 600 (coadd, redrock, sha256sum × bright, dark × 100 pixels); 0 missing | HEAD, HTTP 200 + Content-Length |
| Exact download volume | 82,678,677,592 B (82.68 GB): coadds 38.33 GB bright + 44.19 GB dark, redrock 0.16 GB, sha256sum files negligible | HEAD Content-Length |
| Per pixel pair | min 0.13 GB, median 0.64 GB, max 2.07 GB | `per_pixel.csv` |
| Projected clean galaxies | 97,553 (BGS 45,698, ELG 28,425, LRG 23,430); per chunk in `projected_counts_class_chunk.csv` | step-1 counts (extractor cut, PHOTSYS not cut) |
| Projected package size, with resolution | 34.63 GB | 97,553 × 355,015 B (gate) |
| Projected package size, without resolution | 5.63 GB | 97,553 × 57,715 B (gate) |
| Projected extraction time | 12.17 h serial; 3.04 h with 4 workers if scaling is linear | 100 × 438.0 s per pixel pair (gate, 1 core) |
| Free space, staging (`/home`) | 41.6 GB; needed 13.3 GB for 4 workers (largest pixel pair × 4 + 5 GB margin) | `statvfs`; pass |
| Free space, NAS (`<nas>`) | 16,372 GB; raw files kept 82.7 GB + package 34.6 GB | `statvfs` |
| Thin cells (< 100 objects) | BGS z ≥ 0.6 (all seven chunks); LRG z < 0.2 (57, 86), z ≥ 1.4 (6, 27); ELG none below 100 | `projected_counts_class_chunk.csv` |

Secondary (not part of the addendum's rules; arithmetic on measured numbers):
- Time projected per object instead of per pixel pair: 117,359 class-tagged objects in the drawn 100 (step-3 summary) × (438.0 s / 2,831 objects) = 5.0 h serial. Pixel 21852 holds about 2.4× the clean galaxies of the mean drawn pixel (2,365 vs 976), so the per-pixel-pair rule probably overstates the time.
- Shard volume on the NAS: 117,359 × (999,410,870 B / 2,831) = 41.4 GB.
- Download time at the 9.8–9.9 MB/s measured on the zpix fetch (2026-10-22): about 2.3 h.

## Outputs
| Path | What it is |
|---|---|
| `per_pixel.csv` | per pixel: HTTP status and bytes of the six files, download bytes, step-1 clean count, PHOTSYS fraction, projected package bytes with/without resolution |
| `projected_counts_class_chunk.csv` | class × redshift-chunk projected clean counts (drawn-100 sample) |
| `dry_run_summary.json` | totals, free space, constants used and their source |

## Checks and status
- Gates passed: no missing files; staging free space sufficient for 4 workers. Gates failed: none.
- Change log: none (no code changed for this run).
- Blockers / open items: step-6 blockers from `../2026-10-23-kit-gate/CODE_REVIEW.md` still open (raw-file move not re-verified; multi-pixel chunk group attributes copied from the first shard).
- Needs my decision: approval of step 6 (download 82.7 GB, about 2.3 h at the measured rate) after the review fixes; the metadata ruling for multi-pixel chunks.

## Not interpreted
No physical conclusion is drawn here.
