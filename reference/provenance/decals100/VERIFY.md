# Independent verification — DESI DR1 DECaLS 100-pixel z-chunk package

- **Date:** 2026-10-23
- **Package:** `<nas>/pop-cosmos_DESI_packages/v4_decals_zchunks/` (15 chunk files + manifest.json + MD5SUMS.txt)
- **Sources:** `<nas>/pop-cosmos_DESI_packages/v4_decals_shards/healpix_*.h5` (100 shards), pixel list `desi_kit/pixel_lists/dr1_decals_random100.txt`
- **Projection:** `desi_kit/reference/2026-10-23-dry-run-100/projected_counts_class_chunk.csv`

## Method
Independent verifier. **No code under `desi_kit/` (kit.py, desikit/, DESIGN.md, tests) or `popfit/` was read, imported or run.**
The file layout was discovered with h5py only; all counts are dataset lengths read from the data (not the `n_*` attributes, which were
only cross-checked). All data access was read-only. Python: `<scratch>/2026-10-23-desi-kit/env/bin/python` (h5py, numpy).

Scripts (in `<scratch>/2026-10-23-desi-decals100/verify/`):
- `h5_structure.py` — layout discovery
- `root_attrs.py` — root attributes of each chunk file
- `check_1to4.py` → `check_1to4.json` — checks 1–4 plus shard-side expected counts over all 100 shards
- `sst_absent.py` — which shard-table rows are flagged absent; sums of the table's counts
- `check5_bytes.py` → `check5_bytes.json` — bytewise package-vs-shard comparison
- `md5check.log` — output of `md5sum -c MD5SUMS.txt`

Layout found: each chunk file has groups `BGS`, `ELG`, `LRG`, each with `catalog/` (29 datasets), `photometry/` (19),
`quality/` (13), `spectra/` (flux, ivar, mask, resolution [N,11,7781]), `spectra_attrs/` (8) and a package-only
`source_row`; plus root `wavelength` (7781), `source_metadata`, `units`, and `source_shard_table/`.
Root attributes carry `z_lo`, `z_hi`, `bin_convention='[z_lo, z_hi)'`, `redshift_column='z_redrock'`, `selection='is_galaxy & is_clean_galaxy'`.

## Check 1 — counts per class × chunk vs projection: **PASS**
Every one of the 45 class×chunk cells and all 15 chunk totals match the projection exactly (difference 0 everywhere).
Dataset lengths also equal the files' `n_BGS/n_ELG/n_LRG/n_objects` attributes, and every per-object dataset in a class group has the same row count.

| chunk | BGS pkg | BGS proj | Δ | ELG pkg | ELG proj | Δ | LRG pkg | LRG proj | Δ | total pkg | total proj | Δ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| z<0.1 | 5501 | 5501 | 0 | 608 | 608 | 0 | 57 | 57 | 0 | 6166 | 6166 | 0 |
| 0.1-0.2 | 13536 | 13536 | 0 | 913 | 913 | 0 | 86 | 86 | 0 | 14535 | 14535 | 0 |
| 0.2-0.3 | 15127 | 15127 | 0 | 410 | 410 | 0 | 452 | 452 | 0 | 15989 | 15989 | 0 |
| 0.3-0.4 | 8718 | 8718 | 0 | 195 | 195 | 0 | 2233 | 2233 | 0 | 11146 | 11146 | 0 |
| 0.4-0.5 | 2348 | 2348 | 0 | 164 | 164 | 0 | 3114 | 3114 | 0 | 5626 | 5626 | 0 |
| 0.5-0.6 | 348 | 348 | 0 | 133 | 133 | 0 | 2890 | 2890 | 0 | 3371 | 3371 | 0 |
| 0.6-0.7 | 44 | 44 | 0 | 283 | 283 | 0 | 2900 | 2900 | 0 | 3227 | 3227 | 0 |
| 0.7-0.8 | 18 | 18 | 0 | 1771 | 1771 | 0 | 3621 | 3621 | 0 | 5410 | 5410 | 0 |
| 0.8-0.9 | 3 | 3 | 0 | 3836 | 3836 | 0 | 4035 | 4035 | 0 | 7874 | 7874 | 0 |
| 0.9-1.0 | 1 | 1 | 0 | 4312 | 4312 | 0 | 2462 | 2462 | 0 | 6775 | 6775 | 0 |
| 1.0-1.1 | 2 | 2 | 0 | 3662 | 3662 | 0 | 1050 | 1050 | 0 | 4714 | 4714 | 0 |
| 1.1-1.2 | 5 | 5 | 0 | 3371 | 3371 | 0 | 375 | 375 | 0 | 3751 | 3751 | 0 |
| 1.2-1.4 | 14 | 14 | 0 | 5507 | 5507 | 0 | 122 | 122 | 0 | 5643 | 5643 | 0 |
| 1.4-1.6 | 3 | 3 | 0 | 3140 | 3140 | 0 | 6 | 6 | 0 | 3149 | 3149 | 0 |
| z>=1.6 | 30 | 30 | 0 | 120 | 120 | 0 | 27 | 27 | 0 | 177 | 177 | 0 |
| **all** | **45698** | **45698** | **0** | **28425** | **28425** | **0** | **23430** | **23430** | **0** | **97553** | **97553** | **0** |

Independent cross-check from the sources: over all 100 shards, objects with `is_galaxy & is_clean_galaxy` number
BGS 45698, ELG 28425, LRG 23430 (total 97553) out of 47906 / 44346 / 25107 shard objects; per pixel × class the
package count equals the shard selected count for all 300 cells (0 mismatches). No package object carries a healpix
outside the list, and every listed pixel contributes objects.

## Check 2 — (targetid, program) uniqueness across all 15 files: **PASS**
97553 rows, 97553 unique (targetid, program) pairs, 0 duplicates. (For information: 96025 unique targetids alone,
i.e. 1528 targetids appear under two programs.) Within each class group `spectra_attrs/targetid` equals `catalog/targetid`.

## Check 3 — redshift within chunk edges: **PASS**
Edges parsed from filenames equal the `z_lo`/`z_hi` attributes in all 15 files (zlt0.10 → [-inf, 0.1); z1.60-inf → [1.6, inf)).
0 objects outside [z_lo, z_hi) on `catalog/z_redrock`; 0 NaN redshifts. Lowest z in the package −0.00093 (BGS, z<0.1 file),
highest 1.6976. All package rows have `is_galaxy` and `is_clean_galaxy` true.

## Check 4 — per-shard metadata table (`source_shard_table`): **PASS** (one absent-class row, correctly flagged)
In every chunk file: 100 rows, 100 unique healpix values, set equal to the 100 pixels on the list (missing 0, extra 0);
every dataset has length 100; the root `source_shards` attribute also names exactly the listed pixels.
False-flag counts per attribute (identical in all 15 files): **all BGS, LRG and source_metadata `__valid` flags are true
(0 missing)**; for ELG, `_present` and every `ELG/*__valid` (11 flags) are false for exactly 1 row, healpix 27948.
Shard `healpix_27948.h5` has no `ELG` group at all, so the flag matches the source. Sums of the table's per-shard
`n_objects` (47906 / 44346 / 25107) and `n_clean_galaxy` (45698 / 28425 / 23430) equal the shard-side counts above.

## Check 5 — bytewise package vs shard rows: **PASS**
Pixels drawn with `numpy.random.default_rng(7341).choice(pixel_list, 3, replace=False)` → **19618, 25553, 36330**.
Rows matched on (targetid, program) across all 15 files; for each matched row all 73 per-object datasets common to
both files (catalog, photometry, quality, spectra flux/ivar/mask/resolution, spectra_attrs) were compared by dtype,
shape and raw bytes (`tobytes()` equality, so NaN payloads compared bitwise). Root `wavelength` byte-identical in all 15 files.

| pixel | class | shard objects | selected | in package | exactly once | dup | missing | unselected present | byte mismatches | source_row mismatches |
|---|---|---|---|---|---|---|---|---|---|---|
| 19618 | BGS | 795 | 776 | 776 | 776 | 0 | 0 | 0 | 0 | 0 |
| 19618 | ELG | 304 | 192 | 192 | 192 | 0 | 0 | 0 | 0 | 0 |
| 19618 | LRG | 263 | 253 | 253 | 253 | 0 | 0 | 0 | 0 | 0 |
| 25553 | BGS | 360 | 348 | 348 | 348 | 0 | 0 | 0 | 0 | 0 |
| 25553 | ELG | 884 | 607 | 607 | 607 | 0 | 0 | 0 | 0 | 0 |
| 25553 | LRG | 542 | 508 | 508 | 508 | 0 | 0 | 0 | 0 | 0 |
| 36330 | BGS | 3 | 3 | 3 | 3 | 0 | 0 | 0 | 0 | 0 |
| 36330 | ELG | 144 | 87 | 87 | 87 | 0 | 0 | 0 | 0 | 0 |
| 36330 | LRG | 124 | 112 | 112 | 112 | 0 | 0 | 0 | 0 | 0 |

Totals: 3419 shard objects, 2886 in the package (each exactly once, all bytes equal), 533 legitimately absent.
**Inferred inclusion rule:** a shard object is in the package iff `is_galaxy & is_clean_galaxy` (same class group,
binned on `z_redrock`); this holds with 0 exceptions in these 3 pixels and, at the count level, in all 300 pixel×class cells.
The only package-only dataset is `source_row`, which equals the object's row index in the shard class group for all 2886 rows.
0 package rows of these pixels lack a shard counterpart.

## Optional — `md5sum -c MD5SUMS.txt`: **PASS**
All 16 entries OK (15 chunk files + manifest.json), exit 0.

## Overall verdict: **PASS**
All five checks and the checksum pass. Package total 97553 = projected 97553; every class×chunk difference is 0.
The only irregularity observed is that shard 27948 has no ELG group, which the shard table records as absent.
