# ref22 against the earlier package `v4_zchunks`: accepted differences

`ref22.json` is the regression manifest of the 22-pixel `ref22` batch as packaged by this code
(commit 2bd4d4e, 2026-10-28). Before it was recorded, that output was compared with `v4_zchunks`,
the same 22 pixels packaged by the earlier script `package_desi_zchunks.py` from one merged
extraction file, using the kit gate's check-3 comparator (`reference/provenance/kit-gate/`).

Identical: the 15 chunk files; 44,219 rows (BGS 15,707, ELG 16,835, LRG 11,677); `targetid`
order in every class and chunk; spectra, redshifts and all other photometry, byte for byte.
Differences covered by the gate's declared exclusions: timestamps, writer name, source-file
attributes, the added PHOTSYS column and `source_shard_table/`, directory parts of paths.

Differences outside those exclusions, accepted by the PI on 2026-10-28:

| Item | Difference | Reason accepted |
|---|---|---|
| `<CLASS>/source_row` | 41,854 of 44,219 differ | definition changed: row in the per-pixel shard, not in the merged extraction file |
| `<CLASS>@n_objects, n_clean_galaxy, median_snr, extraction_success_count, n_qso_contamination, n_star_contamination, n_zwarn_nonzero` | present only in `v4_zchunks` | per-shard values now live in `source_shard_table/<CLASS>/` |
| `/source_metadata@creation_date, n_total_objects` | present only in `v4_zchunks` | differ between shards, so kept only in `source_shard_table/source_metadata/` |
| `/source_metadata@n_coadds_processed` | 2 here, 44 in `v4_zchunks` | per-shard value common to all shards (see README, Output format); not a total |
| `<CLASS>/photometry/mw_transmission_{g,r,z,W1,W2}` (float32) | 336–411 of 44,219 values per band differ, each by exactly 1 ULP (max relative 7.5e-8) | within the 1-ULP tolerance of `regress`; pixel 21852 matched kit 0.2.0 byte for byte, so the difference predates this code |
