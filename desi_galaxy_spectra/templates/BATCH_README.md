# DESI DR1 clean galaxies, batch $batch

Redshift-chunk files of the clean galaxies (BGS, ELG, LRG) in $n_pixels HEALPix pixels of the
DESI DR1 `iron` main-survey coadds, extracted and packaged with `desi-galaxy-spectra`
$kit_version (lockfile sha256 `$lockfile_sha256`). Written $written by `$run_by`.

## Contents

| File | What it is |
|---|---|
| `desi_dr1_clean_galaxies_<z range>.h5` | one file per redshift range, `[z_lo, z_hi)` on `z_redrock`; a range with no clean galaxy in this batch has no file |
| `manifest.json` | per-dataset md5, dtype and shape, and an attribute md5, for every chunk file |
| `MD5SUMS.txt` | `md5sum` listing of the chunk files and `manifest.json`; check with `md5sum -c MD5SUMS.txt` |
| `VERIFY.md` | the verification report; the line `RESULT: PASS` means every check passed |
| `RUN_SUMMARY.md`, `run_summary.json` | the run record: provenance, counts, timings, checks |
| `status.json` | stage, timestamps, counts and bytes written by the driver |
| `extract_log.jsonl` | one line per extracted pixel (timings, object counts), when the log was kept |

This batch holds $n_chunk_files chunk files with $n_clean clean galaxies
(BGS $n_BGS, ELG $n_ELG, LRG $n_LRG).

## Layout of a chunk file

- Root: `wavelength` (one common grid), groups `source_metadata`, `units` and
  `source_shard_table`, and attributes `z_lo`, `z_hi`, `bin_convention`, `redshift_column`,
  `selection` (`is_galaxy & is_clean_galaxy`), `source_shards`, `has_resolution`, `n_objects`,
  `n_BGS`, `n_ELG`, `n_LRG`.
- One group per class present (`BGS`, `ELG`, `LRG`) with `catalog/`, `photometry/`, `quality/`,
  `spectra/` (`flux`, `ivar`, `mask`, and `resolution` when `has_resolution` is true),
  `spectra_attrs/`, and `source_row` (the row of the object in its per-pixel shard).
- Objects are identified by the pair (`targetid`, `program`); the pair is unique across the
  whole archive.

## Reading

```python
import h5py
with h5py.File("desi_dr1_clean_galaxies_z0.10-0.20.h5") as f:
    wave = f["wavelength"][()]
    z = f["BGS/catalog/z_redrock"][()]
    flux = f["BGS/spectra/flux"][0]
```

Selection, redshift ranges and layout are those of the frozen packager; the checks that
`VERIFY.md` reports are described in the `desi_galaxy_spectra.verify` module.
