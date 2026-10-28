#!/bin/bash
# Package and verify one batch whose shards are already extracted (the batch must be claimed by
# you; extract_batch.sbatch claims it).  Cuts the redshift chunks, writes the manifest and checks
# the result against the shards.  Stops at `verified`; run `dgs deliver --batch ID` afterwards.
#
# Inputs: the settings file (DGS_SETTINGS) and the batch id (DGS_BATCH, else the position
# SLURM_ARRAY_TASK_ID in the list of batch ids, as in extract_batch.sbatch).
#
# Submit from the repository checkout, for example
#   DGS_SETTINGS=$PWD/settings.toml DGS_BATCH=007 sbatch slurm/package_verify_batch.sbatch
#
#SBATCH --job-name=dgs-package-verify
#SBATCH --time=24:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --output=slurm-start-%A_%a.log
# Site-specific flags are filled in by the site notes; leave them commented until then.
# #SBATCH --partition=<partition>
# #SBATCH --account=<project-account>
set -euo pipefail
umask 002

: "${DGS_SETTINGS:?set DGS_SETTINGS to the settings TOML file}"
export DGS_SETTINGS
cd "${SLURM_SUBMIT_DIR:-.}"

export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1

if [ -n "${DGS_BATCH:-}" ]; then
    ID="$DGS_BATCH"
else
    : "${SLURM_ARRAY_TASK_ID:?give DGS_BATCH or submit with --array}"
    ID="$(uv run --frozen python -c 'import sys; from desi_galaxy_spectra.batches import batch_ids; print(batch_ids()[int(sys.argv[1])])' "$SLURM_ARRAY_TASK_ID")"
fi
BATCH_ROOT="$(uv run --frozen python -c 'from desi_galaxy_spectra.settings import load_settings; print(load_settings().batch_root)')"

mkdir -p "$BATCH_ROOT/batch_$ID"
exec >>"$BATCH_ROOT/batch_$ID/slurm-${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-0}}_${SLURM_ARRAY_TASK_ID:-0}.log" 2>&1

echo "batch $ID on $(hostname) at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
uv run --frozen dgs run --batch "$ID" --until verified
echo "done at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
