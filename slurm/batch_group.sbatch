#!/bin/bash
# Run a group of batches in one allocation, from downloaded raw files to `verified`.  The batches
# are extracted one after another with all the workers of the settings (each is claimed first);
# as soon as one is extracted its packaging and verification start in the background, so they
# overlap the extraction of the next batch.  The job ends when every batch is verified or has
# failed, and fails if any batch failed.  Run `dgs deliver --batch ID` afterwards.
#
# Inputs: the settings file (DGS_SETTINGS) and the batch ids (DGS_BATCH, separated by spaces).
#
# Submit from the repository checkout, for example
#   DGS_SETTINGS=$PWD/settings.toml DGS_BATCH="001 002 003 004" sbatch slurm/batch_group.sbatch
# A batch takes about 6 min to extract on 64 workers; group about four to eight batches per job.
#
#SBATCH --job-name=dgs-batch-group
#SBATCH --time=24:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=72
# Memory is a site flag (commented): see extract_batch.sbatch.  One extraction (64 workers x
# 1.36 GB) plus one packaging run (at most 16 processes, under 1 GB each) is about 100 GB.
# #SBATCH --mem=110G
#SBATCH --output=slurm-group-%j.log
# Site-specific flags are filled in by the site notes; leave them commented until then.
# #SBATCH --partition=<partition>
# #SBATCH --account=<project-account>
set -euo pipefail
umask 002

: "${DGS_SETTINGS:?set DGS_SETTINGS to the settings TOML file}"
: "${DGS_BATCH:?set DGS_BATCH to the batch ids, separated by spaces}"
export DGS_SETTINGS
cd "${SLURM_SUBMIT_DIR:-.}"

# One thread per worker: the work parallelises over pixels and chunk files, not inside numpy.
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1

BATCH_ROOT="$(uv run --frozen python -c 'from desi_galaxy_spectra.settings import load_settings; print(load_settings().batch_root)')"
echo "batches $DGS_BATCH on $(hostname) at $(date -u +%Y-%m-%dT%H:%M:%SZ)"

pids=()
ids=()
failed=()
for ID in $DGS_BATCH; do
    mkdir -p "$BATCH_ROOT/batch_$ID"
    LOG="$BATCH_ROOT/batch_$ID/slurm-${SLURM_JOB_ID:-0}_group.log"
    echo "batch $ID: extract from $(date -u +%Y-%m-%dT%H:%M:%SZ), log $LOG"
    if uv run --frozen dgs run --batch "$ID" --claim --until extracted >>"$LOG" 2>&1; then
        echo "batch $ID: extracted at $(date -u +%Y-%m-%dT%H:%M:%SZ); package and verify in the background"
        uv run --frozen dgs run --batch "$ID" --until verified >>"$LOG" 2>&1 &
        pids+=("$!")
        ids+=("$ID")
    else
        echo "batch $ID: extraction FAILED, not packaged"
        failed+=("$ID")
    fi
done

for i in "${!pids[@]}"; do
    pid="${pids[$i]}"
    if wait "$pid"; then
        echo "batch ${ids[$i]}: verified"
    else
        echo "batch ${ids[$i]}: package or verify FAILED"
        failed+=("${ids[$i]}")
    fi
done

echo "done at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
if [ "${#failed[@]}" -gt 0 ]; then
    echo "failed batches: ${failed[*]}"
    exit 1
fi
