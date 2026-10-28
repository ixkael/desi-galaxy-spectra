# desi-galaxy-spectra

Extraction of the clean galaxy spectra of DESI DR1 (`iron` release, main survey) into
redshift-chunk HDF5 archives, in fixed batches that several people can run side by side on a
shared file tree. The command is `dgs`.

For each HEALPix pixel the tool downloads the coadd and redrock files, checks their sha256,
cuts out the galaxies that pass the selection below, stacks them in per-pixel shards, and
packages the shards of a batch into 15 redshift-chunk files with a manifest, a checksum list and
a verification report.

## Install

Install is a repository checkout with `uv sync`:

```
git clone https://github.com/ixkael/desi-galaxy-spectra
cd desi-galaxy-spectra
uv sync --frozen
uv run dgs --help
```

A non-editable wheel install (`pip install .` into another environment) is not supported. The
batch lists (`batches/`), the lockfile (`uv.lock`, `requirements.lock.txt`) and the references
(`reference/`, `pixel_lists/`) are repository data that the code reads from the checkout, and a
wheel does not carry them.

Python is 3.10 or 3.11. The environment is locked for Linux x86_64, Linux aarch64 and macOS
arm64. On aarch64 the `fitsio` 1.4.2 package is built from source, which needs a C compiler and
the curl, zlib and bzip2 development headers (the CI workflow shows the Debian packages).
Floating-point results on aarch64 agree with x86_64 to within one unit in the last place; the
verification tolerances allow for that.

## Settings

All site-specific paths live in a TOML file. `dgs` reads the file given by `--settings`, else
`$DGS_SETTINGS`, else `./settings.toml` (which git ignores). Path values expand `$VAR` and `~`;
an unset variable is an error.

| Key | Meaning |
|---|---|
| `raw_root` | the raw DESI files, in the layout of the public archive |
| `shard_root` | per-pixel shards, one directory `batch_<id>` per batch |
| `batch_root` | packaged batches, one directory `batch_<id>` per batch, plus claims and status |
| `staging` | fast scratch for streaming downloads and temporary files |
| `workers` | extraction processes (default 1) |
| `keep_raw_after_verify`, `keep_shards_after_verify` | keep (true) or remove the inputs once a batch is verified |
| `with_resolution` | write the resolution matrices into the chunk files (default true; about six times larger) |
| `reference_root` | location of the regression references, needed only by `dgs regress` |
| `group` | Unix group of the shared tree, checked by `dgs doctor` |

`settings.example-isambard.toml` is a worked example for a cluster node with 64 workers
(64 workers at a peak of about 1.36 GB each is about 87 GB, below the 115 GB of the node slice).

## Output format and selection

An object is kept when it passes all of these (design decision 2 of the build):

- it is in the DESI DR1 main survey, in either the bright or the dark programme;
- its class is read from the `DESI_TARGET` bits with priority LRG, then ELG, then BGS;
  objects with none of the three bits are dropped;
- `SPECTYPE == GALAXY`, `ZWARN == 0` and `DELTACHI2 > 25`;
- BGS targets observed in a dark-programme tile are kept (the class comes from the bits, not
  from the programme);
- repeat observations of the same target are kept as separate rows. An object is identified by
  the pair (`targetid`, `program`), and that pair is unique across the archive (`dgs index`
  checks it).

Each batch directory `<batch_root>/batch_<id>/` holds `desi_dr1_clean_galaxies_<z range>.h5`
(one file per half-open redshift range on the redrock redshift, 15 ranges; a range with no
object has no file), `manifest.json`, `MD5SUMS.txt`, `VERIFY.md`, `RUN_SUMMARY.md`,
`run_summary.json`, `status.json` and a `README.md` describing the layout of the chunk files and
how to read them with `h5py`.

## Thin redshift chunks

A batch holds about 100 pixels, drawn at random from the eligible pixels, and the 15 redshift
chunks are cut over the whole batch. Some class by chunk cells hold very few objects: in the
100-pixel draw (`reference/pixel_list/RUN_SUMMARY.md`) LRG at z < 0.1 has 57 objects, LRG at
z >= 1.6 has 27, and BGS above z = 0.6 has between 1 and 44 per chunk. This costs nothing in
storage, since the full archive is the union of all batches. It matters if you analyse a single
batch on its own: a cell with fewer than 100 objects gives a noisy mean spectrum or a noisy
redshift distribution, and the redshift distribution of one batch is not that of the survey. For
population statistics, combine batches.

## Batch workflow

The 84 batches `000` to `083` and the 22-pixel reference batch `ref22` are fixed lists in
`batches/`. Several people share one tree, and a claim file keeps two people off the same batch.

```
dgs next                          # lowest batch that is neither claimed nor delivered
dgs claim --batch 007             # exclusive; fails if someone else holds it
dgs run --batch 007 --fetch --until verified
dgs deliver --batch 007           # mark it delivered
dgs release --batch 007           # give up a claim without finishing (--force: someone else's)
dgs status [--deep]               # table over all batches (--deep re-hashes the chunk files)
```

`dgs run` takes a claimed batch from the stage found on disk to `--until` one of `downloaded`,
`extracted`, `packaged`, `verified`, `delivered`. A failure leaves the batch at its stage, and a
rerun resumes from there. `dgs run --claim` claims first. The stage is recomputed from markers on
disk each time, so an interrupted job can simply be resubmitted.

Things to know:

- `dgs next` trusts the `DELIVERED` marker: it does not re-hash a delivered batch. Use
  `dgs status --deep` to re-check the files.
- There are two delivery paths, `dgs deliver --batch ID` and
  `dgs run --batch ID --until delivered`. Both run the deep check first and write `DELIVERED`
  only if it passes.
- `dgs verify` needs an explicit `--shard-dir DIR` (the checks that compare chunks to shards) or
  `--no-shards`. It does not take the directory from the settings.
- `dgs regress --reference {21852,batch000,ref22} --scratch PATH` reruns a reference in a
  scratch directory and compares it with the stored result. It needs the regression references
  (`reference/regress/<stem>.json` for the stems `pixel21852`, `batch000` and `ref22`, with the
  arrays under `reference_root/<stem>/`), which are not part of a plain checkout; get them from
  the maintainers and point `reference_root` at them.

### Slurm

`slurm/` has templates that take only two inputs, the settings file (`DGS_SETTINGS`) and the
batch id (`DGS_BATCH`, or the position in the list of batch ids from `--array`):

| Template | What it does |
|---|---|
| `extract_batch.sbatch` | `dgs run --batch ID --claim --until extracted`, with the worker count from the settings |
| `package_verify_batch.sbatch` | `dgs run --batch ID --until verified` |
| `extract_pixel_array.sbatch` | one pixel per array task, to spread a batch over nodes |
| `fetch_test.sbatch` | download one pixel into a test root and print bytes and seconds |

Partition, account and GPU-slice flags are commented placeholders to fill in for your site. Logs
go to `<batch_root>/batch_<id>/slurm-<job>_<task>.log`.

## Shared tree and group membership

Every collaborator who runs batches needs membership of the shared Unix group that owns the
tree. The data directories are setgid so new files inherit the group, and every job runs with
`umask 002` so group members can write each other's outputs. `dgs doctor` lists the paths under
the three data roots that a group member could not write to, and reports; it never changes a
permission. Set `group` in the settings to make it check the group as well.

## Repository layout

| Path | Contents |
|---|---|
| `desi_galaxy_spectra/` | the package; `frozen/` holds the selection and packaging code carried over unchanged from the earlier private tool |
| `batches/`, `pixel_lists/` | the fixed batch lists and the pixel table they were drawn from |
| `reference/pixel_list/` | the script that built the pixel list (`--zpix-dir` is the directory with the DR1 `zpix-main-*.fits` catalogues) and its run summary |
| `reference/provenance/` | run summaries and checks from the validation of the earlier private tool (written when it was still called `desikit`); text there may use its old file names |
| `slurm/`, `settings.example-isambard.toml` | job templates and an example settings file |
| `tools/hygiene_grep.sh` | refuses to pass if a tracked file names a private path, machine, credential or internal file; CI runs it |
| `tests/` | `uv run pytest -q` |

## Development

```
uv sync --frozen
uv run pytest -q
uv run ruff check
bash tools/hygiene_grep.sh
```

CI runs these four on `ubuntu-24.04` and `ubuntu-24.04-arm` with Python 3.10.

## Licence

BSD 3-Clause, see `LICENSE`. DESI DR1 data are released under the DESI data licence; see
https://data.desi.lbl.gov/doc/ for the terms and the acknowledgement text.
