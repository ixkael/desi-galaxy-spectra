# Changelog

## 1.0.0

First public release.

- `dgs` command line: `fetch`, `extract`, `package`, `manifest`, `verify`, `run`, `claim`,
  `release`, `next`, `status`, `doctor`, `deliver`, `index`, `regress`.
- Fixed list of 84 batches of about 100 HEALPix pixels each (`batches/`), plus the 22-pixel
  reference batch `ref22`, drawn with a recorded seed from the pixel list in `pixel_lists/`.
- Redshift-chunk HDF5 output (15 chunks, class-tagged BGS/ELG/LRG, resolution matrices optional),
  manifest, `MD5SUMS.txt` and a verification report per batch.
- Shared-tree workflow: exclusive batch claims, group-writable outputs (`umask 002`, setgid
  directories), `dgs doctor`, `dgs status`, `dgs index`.
- Slurm templates (`slurm/`) and an example settings file for a cluster node
  (`settings.example-isambard.toml`).
- Locked environment (`uv.lock`, `requirements.lock.txt`) for x86_64 and aarch64 Linux and
  arm64 macOS; CI on `ubuntu-24.04` and `ubuntu-24.04-arm`.
- Provenance of the validation runs of the earlier private tool in `reference/provenance/`, and
  the pixel-list builder in `reference/pixel_list/`.
- `tools/hygiene_grep.sh`: refuses private paths, machine names, credentials and internal files
  in anything tracked or not ignored.
