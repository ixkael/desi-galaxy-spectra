#!/bin/bash
# Extract the pixels of one batch as a Slurm array, one pixel per array task.  Use it to spread a
# batch over several nodes; each task writes its shards into the batch's shard directory.  Run
# `dgs run --batch ID --claim --until extracted` (extract_batch.sbatch) afterwards: it finds the
# shards that exist and skips them.
#
# Inputs: the settings file (DGS_SETTINGS) and the batch id (DGS_BATCH, required).  The array
# index is the position of the pixel in the batch list (0 to 99 for a batch of 100 pixels).
#
# Submit from the repository checkout, for example
#   DGS_SETTINGS=$PWD/settings.toml DGS_BATCH=007 sbatch --array=0-99 slurm/extract_pixel_array.sbatch
#
#SBATCH --job-name=dgs-extract-pixel
#SBATCH --time=24:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
# On Isambard-AI every array task is allocated (and charged) a whole 72-core GH200 slice; prefer
# extract_batch.sbatch there.  Explicit memory: see extract_batch.sbatch.
# #SBATCH --mem=8G
#SBATCH --output=slurm-start-%A_%a.log
# Site-specific flags are filled in by the site notes; leave them commented until then.
# #SBATCH --partition=<partition>
# #SBATCH --account=<project-account>
set -euo pipefail
umask 002

: "${DGS_SETTINGS:?set DGS_SETTINGS to the settings TOML file}"
: "${DGS_BATCH:?set DGS_BATCH to the batch id}"
: "${SLURM_ARRAY_TASK_ID:?submit with --array}"
export DGS_SETTINGS
cd "${SLURM_SUBMIT_DIR:-.}"

export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1

ID="$DGS_BATCH"
PIXEL="$(uv run --frozen python -c 'import sys; from desi_galaxy_spectra.batches import read_batch; print(read_batch(sys.argv[1])[int(sys.argv[2])])' "$ID" "$SLURM_ARRAY_TASK_ID")"
BATCH_ROOT="$(uv run --frozen python -c 'from desi_galaxy_spectra.settings import load_settings; print(load_settings().batch_root)')"
SHARD_ROOT="$(uv run --frozen python -c 'from desi_galaxy_spectra.settings import load_settings; print(load_settings().shard_root)')"

mkdir -p "$BATCH_ROOT/batch_$ID" "$SHARD_ROOT/batch_$ID"
exec >>"$BATCH_ROOT/batch_$ID/slurm-${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-0}}_${SLURM_ARRAY_TASK_ID}.log" 2>&1

echo "batch $ID pixel $PIXEL on $(hostname) at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
uv run --frozen dgs extract --pixel "$PIXEL" --shard-dir "$SHARD_ROOT/batch_$ID" --workers 1
echo "done at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
