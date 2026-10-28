# Run summary: 2026-10-23-desi-pixel-list

Written by the engineer role. Facts and diagnostics only. Step 1 of
`plans/2026-10-22-desi-more-data.md`. A field that could not be produced says
`not produced: <reason>`.

## Provenance
| Field | Value |
|---|---|
| Repo, commit | `pop-cosmos_DESI` @ `0e3f14d9` (dirty: yes — script and this directory uncommitted; script sha256 `f223720f…e1ed` in the JSON sidecar) |
| Command | `OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 <home>/anaconda3/bin/python scripts/build_desi_pixel_list.py --cache <scratch>/2026-10-23-desi-pixel-list/cache --out desi_kit/pixel_lists` (from the repo root) |
| Profile | engineer |
| Machine, environment | x86_64 workstation, `<home>/anaconda3` (python 3.10, numpy 2.2.6, astropy 6.1.7) |
| Random seeds | 20261023 (`numpy.random.default_rng`) |
| Inputs | `<nas>/desi_dr1/zcatalog/v1/zpix-main-bright.fits` (11,020,470 rows, 9,425,099,520 B, sha256 127dcc30…8750); `…/zpix-main-dark.fits` (12,778,525 rows, 11,289,738,240 B, sha256 da8cc3f8…0a68). Hashes from `redux_iron_zcatalog_v1.sha256sum`, `sha256sum -c` PASS 2026-10-22 18:27; not recomputed. |
| Wall time | not produced: not timed (a few minutes; NAS-bound sequential read) |

## Model and configuration
- Stage: pixel selection + clean-galaxy counting; no model, no free parameters.
- Selection, copied from `extract_desi_dr1_final.py`: class from DESI_TARGET bits (l.61–64) with
  priority LRG > ELG > BGS (`assign_class`, l.107–115; rows with none of the three bits dropped);
  clean galaxy = SPECTYPE == 'GALAXY' & ZWARN == 0 & DELTACHI2 > 25 (l.75, l.449–451). Both
  programmes counted, as the extractor walks both coadds: a dark-programme row whose only class bit
  is BGS_ANY counts as BGS (the drop at l.302–319 applies only when no bright coadd exists). Those
  rows are tallied separately as "BGS from dark".
- Redshift chunks: `EDGES` imported from `scripts/package_desi_zchunks.py` (15 half-open chunks in Z).
- Variant counts: (a) extractor cut; (b) (a) + ZCAT_PRIMARY + COADD_FIBERSTATUS == 0; each also
  with a per-object PHOTSYS == 'S' requirement.
- Pixel eligibility: present in both zpix files (bright and dark coadd exist); fraction of clean,
  class-tagged galaxies with PHOTSYS == 'S' ≥ 0.95; not one of the 22 v4 pixels.
- Reader: raw big-endian memory map, 200,000-row blocks, 10 columns kept.

| # | Cut | Rows before | Rows after | Reference |
|---|---|---|---|---|
| 1 | pixel in both programmes | 13,994 bright / 13,494 dark pixels | 11,094 pixels | zpix HEALPIX |
| 2 | ≥ 1 clean class-tagged galaxy | 11,094 | 11,085 | — |
| 3 | frac(PHOTSYS='S') ≥ 0.95 | 11,085 | 8,434 (all at exactly 1.0) | 2,480 at 0, 171 between 0 and 0.95, none in [0.95, 1) |
| 4 | not in the existing 22 | 8,434 | 8,412 eligible | memory `desi-data-locations` |
| 5 | seeded draw | 8,412 | 100 | `dr1_decals_random100.txt` |

## Best-fit parameters
Not applicable (no fit).

## Diagnostics (physical first)
| Diagnostic | Value | Threshold or reference | Pass? |
|---|---|---|---|
| 22-pixel recount, total | 44,219 (BGS 15,707 / ELG 16,835 / LRG 11,677) | v4_zchunks README/RUN_SUMMARY: 44,219 | yes |
| 22-pixel recount, 45 class × chunk cells | all identical | v4_zchunks README table | yes |
| BGS from dark in the 22 | 1 (it is in v4's 15,707) | — | noted |
| Per-object PHOTSYS='S' changes counts? | no change in any sample | — | — |
| + ZCAT_PRIMARY & COADD_FIBERSTATUS==0: drawn 100 | 97,553 → 97,552 (one BGS, z 0.1–0.2) | — | — |
| + ZCAT_PRIMARY & COADD_FIBERSTATUS==0: existing 22 | 44,219 → 44,219 | — | — |
| + ZCAT_PRIMARY & COADD_FIBERSTATUS==0: all eligible | 8,694,127 → 8,693,618 (−509) | — | — |
| HEALPIX range assertion (nside 64) | passed | 0 ≤ hpx < 49,152 | yes |
| Pixel totals per programme | 13,994 bright / 13,494 dark | plan §0 directory counts 13,994 / 13,494 | yes |

Note: 1,143 targets of the 22 pixels have both a bright and a dark spectrum (v4_zchunks
RUN_SUMMARY) and ZCAT_PRIMARY did not remove any of them, so in these per-programme files
ZCAT_PRIMARY behaves as primary within the file, not across programmes.

### Counts, drawn 100 pixels, extractor cut (total 97,553; BGS 45,698 / ELG 28,425 / LRG 23,430; BGS from dark 6)
| Chunk | BGS | ELG | LRG | Total |
|---|---|---|---|---|
| z<0.1 | 5501 | 608 | **57** | 6166 |
| 0.1–0.2 | 13536 | 913 | **86** | 14535 |
| 0.2–0.3 | 15127 | 410 | 452 | 15989 |
| 0.3–0.4 | 8718 | 195 | 2233 | 11146 |
| 0.4–0.5 | 2348 | 164 | 3114 | 5626 |
| 0.5–0.6 | 348 | 133 | 2890 | 3371 |
| 0.6–0.7 | **44** | 283 | 2900 | 3227 |
| 0.7–0.8 | **18** | 1771 | 3621 | 5410 |
| 0.8–0.9 | **3** | 3836 | 4035 | 7874 |
| 0.9–1.0 | **1** | 4312 | 2462 | 6775 |
| 1.0–1.1 | **2** | 3662 | 1050 | 4714 |
| 1.1–1.2 | **5** | 3371 | 375 | 3751 |
| 1.2–1.4 | **14** | 5507 | 122 | 5643 |
| 1.4–1.6 | **3** | 3140 | **6** | 3149 |
| z≥1.6 | **30** | 120 | **27** | 177 |
Bold: fewer than 100 objects (thin). No chunk total is below 100. All variants and the other
two samples: `counts_class_chunk.csv`, `counts_summary.json`.

### Projected sizes
| Sample | Pixels | Coadds | Download (322/406 MB mean) | Clean galaxies | Packaged (× 0.354 MB) |
|---|---|---|---|---|---|
| drawn 100 | 100 | 200 | 72.8 GB | 97,553 | 34.5 GB |
| all eligible | 8,412 | 16,824 | 6.12 TB | 8,694,127 | 3.08 TB |

Clean galaxies per pixel: drawn mean 975.5 (min 139, max 2,586); all eligible 1,033.5; existing 22: 2,010.

## Outputs
| Path | What it is |
|---|---|
| `dr1_decals_random100.txt` | the 100 drawn pixels (HEALPix nside 64 nested), sorted |
| `dr1_decals_random100.json` | sidecar: seed, rule, eligibility stats, input sha256s, script sha256, repo HEAD |
| `pixel_photsys_table.csv` | per pixel: clean counts, PHOTSYS='S' count and fraction, flags (both programmes, existing, eligible, drawn) |
| `counts_class_chunk.csv` | long table: sample × variant × class × chunk |
| `counts_summary.json` | same counts as nested JSON |
| `RUN_SUMMARY.md`, `run_summary.json` | this summary |
| `<scratch>/2026-10-23-desi-pixel-list/cache/zpix_{bright,dark}_compact.npz` | scratch cache of the kept columns (not a deliverable) |

## Checks and status
- Gates passed: 22-pixel recount (total and every class × chunk cell); HEALPIX range; pixel totals.
- Change log: first run read the columns in little-endian order (astropy `columns.dtype` is native
  order), so every value was scrambled and the script's own check that the 22 pixels exist stopped
  it before any output. Fixed with `.newbyteorder(">")`, added a HEALPIX range assertion, deleted
  the cache, reran. No cut changed.
- desispec version used for v4: not produced — `metadata` attrs of v4.h5 carry no desispec version.
- Blockers / open items: none for step 1.
- Needs my decision: whether "BGS from dark" rows stay in (extractor behaviour; 1 in the 22, 6 in
  the drawn 100). Keeping them is what reproduces v4.

## Not interpreted
No physical conclusion is drawn here.
