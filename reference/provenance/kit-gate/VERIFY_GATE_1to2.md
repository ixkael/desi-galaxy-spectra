# Independent verification: kit gate checks 1 and 2, HEALPix 21852

Date 2026-10-23. This is a read-only check written from scratch with h5py and numpy only. No kit code was read or imported.
- Script: `compare_gate_1to2.py`. Full results: `compare_gate_1to2.json`. Both are in this directory. Run time was 60 s.
- Reference: `<nas>/pop-cosmos_DESI_packages/kit_reference/desispec070_pixel21852.h5`. Its md5 is `20a4a9148dc13ccb0a9ca2a8ebb28b2d`, as expected; this was checked before the run.
- Kit temporary file in the v4 layout: `.../kit_gate_21852/v4layout_21852.h5`
- Kit shard: `.../kit_gate_21852/healpix_21852.h5`

## Verdict

**Check 1: PASS. Check 2: PASS.** No element differs anywhere, apart from the two declared exclusions and the approved shard additions.

## Method

"Equal" means the same dtype, the same shape and identical raw bytes, tested with `np.array_equal` on uint8 views. For an attribute, the Python/numpy type must also be the same.

Sensitivity control: on a copy held in memory, the last mantissa bit of one flux element was flipped, which is a change of 1 ULP (unit in the last place). Both cases below were run on the actual data.
- Reference ELG `obj_00010`, element 3000: −0.16499138 changed to −0.16499139, |Δ| = 1.5e-8. The comparator flags it (1 element). The unmodified copy compares equal.
- Shard LRG row 5, element 3000, compared against reference `obj_00005`: the comparator flags it (1 element). The unmodified row compares equal.

## Check 1: the temporary v4-layout file against the reference

This is a full recursive walk of both files covering every group, every dataset and every attribute, the root attributes included.

| quantity | count |
|---|---|
| paths present in only one file | 0 (reference only: 0, v4 layout only: 0) |
| groups compared (attributes included) | 2,849 |
| attributes compared | 22,707, with 0 differences |
| datasets compared | 14,335 (2,831 objects × 5 spectral datasets, plus 180 table columns) |
| elements compared | 330,615,504, with 0 differences |
| storage-layout mismatches (dtype, shape, chunks, gzip level, shuffle, fletcher32, scaleoffset, fill value, filter list) | 0 |

Objects per class, the same in both files: BGS 867, ELG 1337, LRG 627 (2,831 in total).

Storage, identical in both files:
- Per-object flux, ivar and wavelength: float32 (7781,), chunks (1946,), gzip 4, no shuffle.
- mask: int16, chunks (3891,).
- resolution: float32 (11, 7781), chunks (2, 1946), gzip 4.
- All 180 table columns are contiguous and unfiltered.

The only exclusions applied:
1. The attribute `metadata/creation_date`: 07:46:36 in the reference, 08:28:06 in the v4-layout file.
2. `{BGS,ELG,LRG}/catalog/coadd_file`. The full paths differ (`<scratch>/2026-10-23-desi-step3/coadds21852/` in the reference, `<nas>/pop-cosmos_DESI_data_backup/desi_dr1_healpix/` in the v4 layout). The basenames are equal in every row.

## Check 2: shard row against reference `obj_i`

**How rows were mapped, derived from the files themselves:**
- In the reference, `spectra/obj_NNNNN` corresponds to catalogue row NNNNN. The object's `targetid` attribute equals `catalog/targetid[NNNNN]` for every object (0 mismatches). The object names are exactly obj_00000 to obj_{n−1}.
- In the shard, row j of every stacked array corresponds to catalogue row j. `spectra_attrs/targetid[j]` equals `catalog/targetid[j]` (0 mismatches).
- Objects were matched on the key (class, targetid, program). The key is unique in both files.
- The match is a bijection in every class, and the permutation is the identity: **shard row i is reference obj_i**.

| class | reference objects | shard rows | bijection | identity | programmes |
|---|---|---|---|---|---|
| BGS | 867 | 867 | yes | yes | bright 867 |
| ELG | 1337 | 1337 | yes | yes | dark 1337 |
| LRG | 627 | 627 | yes | yes | bright 64, dark 563 |

**Spectral arrays.** The comparison is row by row; the root `wavelength` was compared with each object's own wavelength.

| dataset | dtype (both) | rows | elements | differing |
|---|---|---|---|---|
| flux | float32 | 2831 | 22,028,011 | 0 |
| ivar | float32 | 2831 | 22,028,011 | 0 |
| mask | int16 | 2831 | 22,028,011 | 0 |
| resolution | float32 | 2831 | 242,308,121 | 0 |
| wavelength (root, shape (7781,), f4) | float32 | 2831 | 22,028,011 | 0 |

**Other comparisons:**
- `spectra_attrs/<attr>` against the per-object attributes: all 8 attributes in each of the 3 classes are equal, and so are the dtypes. The float64 ones are arm_B/R/Z_coverage and snr_median, the bool is extraction_success, and the int64 ones are n_pixels, resolution_n_diag and targetid.
- Table columns (catalog, photometry, quality): all 180 shared columns are byte-equal, with the same storage, after the `coadd_file` basename rule. They include the `mw_transmission_*` columns. Those showed 1-ULP differences in the earlier v4 comparison, but here they are identical.
- Class-group attributes (10 per class): all equal.
- `metadata` attributes (19) and `metadata/units` attributes (11): all equal, excluding `creation_date`.
- Totals: 14,359 datasets or rows compared, 330,638,152 elements, 0 differences.
- Exclusions: the same two as in check 1, and nothing else.

**Keys present only in the shard.** Every one of them is an approved addition or a layout change set out in DESIGN.md "Shard". Nothing unexpected was found. Nothing is present in the reference but missing from the shard, apart from the per-object groups that the stacked arrays replace.
- `catalog/photsys` in each class. It is dtype S1 and every value is 'S': BGS 867, ELG 1337, LRG 627. The values are a valid subset of {N, S, G}. 'S' fits this southern pixel and the DESIGN ruling that the 100 drawn objects are 100% 'S'. There is no reference counterpart, so these values are not verified against anything.
- Root `wavelength`, `spectra/{flux,ivar,mask,resolution}` and the 8 `spectra_attrs/*` datasets.
- Stacked storage: chunks (1, 7781) or (1, 11, 7781), gzip 4.
- Root provenance attributes. The reference has no root attributes.
  - healpix=21852 (int64), kit_version=0.1.0, desispec_version=0.70.0, numpy_version=2.2.6.
  - python_version=3.10.20. This one is not in the DESIGN list, but it is a harmless provenance field.
  - lockfile_sha256=b035c5b8…49cd.
  - input_files: the 4 basenames.
  - input_sha256: 4 values. **All 4 were checked independently with `sha256sum` and match the actual inputs**: the two NAS coadds and the two `data/redrock_cache` redrock files.

## Notes

- Nothing surprising was found.
- Only the decoded array values and the storage settings were compared. The raw compressed on-disk bytes were not compared.
- The lockfile sha256 was not recomputed here.
