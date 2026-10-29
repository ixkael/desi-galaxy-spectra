#!/bin/bash
# Extract one batch (about 100 HEALPix pixels) into shards: download-checked raw files in, shards out.
#
# Inputs: the settings file (DGS_SETTINGS) and the batch id.  The batch id is DGS_BATCH if set,
# else the position SLURM_ARRAY_TASK_ID in the list printed by `dgs.batches.batch_ids()`
# (index 0 is batch 000, index 83 is batch 083, index 84 is ref22).
#
# Submit from the repository checkout, for example
#   DGS_SETTINGS=$PWD/settings.toml sbatch --array=0-3 slurm/extract_batch.sbatch
#   DGS_SETTINGS=$PWD/settings.toml DGS_BATCH=007 sbatch slurm/extract_batch.sbatch
# The worker count is the `workers` key of the settings (64 in settings.example-isambard.toml:
# 64 workers x 1.36 GB peak is about 87 GB, under the 115 GB of one node slice).
#
#SBATCH --job-name=dgs-extract
#SBATCH --time=24:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=64
# Memory is a site flag (commented): without it Isambard-AI gives the job the whole node's memory, and
# the charge follows the largest of the GPU, CPU and memory shares (one GH200 slice = 72 cores, 115 GB).
# #SBATCH --mem=110G
#SBATCH --output=slurm-start-%A_%a.log
# Site-specific flags are filled in by the site notes; leave them commented until then.
# #SBATCH --partition=<partition>
# #SBATCH --account=<project-account>
# #SBATCH --gres=<gpu-slice-or-none>
set -euo pipefail
umask 002

: "${DGS_SETTINGS:?set DGS_SETTINGS to the settings TOML file}"
export DGS_SETTINGS
cd "${SLURM_SUBMIT_DIR:-.}"

# One thread per worker: the extraction parallelises over pixels, not inside numpy.
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1

if [ -n "${DGS_BATCH:-}" ]; then
    ID="$DGS_BATCH"
else
    : "${SLURM_ARRAY_TASK_ID:?give DGS_BATCH or submit with --array}"
    ID="$(uv run --frozen python -c 'import sys; from desi_galaxy_spectra.batches import batch_ids; print(batch_ids()[int(sys.argv[1])])' "$SLURM_ARRAY_TASK_ID")"
fi
BATCH_ROOT="$(uv run --frozen python -c 'from desi_galaxy_spectra.settings import load_settings; print(load_settings().batch_root)')"

# From here on everything goes to the log beside the batch outputs.
mkdir -p "$BATCH_ROOT/batch_$ID"
exec >>"$BATCH_ROOT/batch_$ID/slurm-${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-0}}_${SLURM_ARRAY_TASK_ID:-0}.log" 2>&1

echo "batch $ID on $(hostname) at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
uv run --frozen dgs run --batch "$ID" --claim --until extracted
echo "done at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
