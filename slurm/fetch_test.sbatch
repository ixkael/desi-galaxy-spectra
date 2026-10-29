#!/bin/bash
# Download test: fetch the raw files of one pixel (21852, the reference pixel) into a throw-away
# raw root under the staging directory, and print how many bytes arrived and how many seconds it
# took.  Use it to learn whether a compute node can reach the archive and how fast.
#
# Input: the settings file (DGS_SETTINGS).  The test root is removed at the end.
#
# Submit from the repository checkout, for example
#   DGS_SETTINGS=$PWD/settings.toml sbatch slurm/fetch_test.sbatch
#
#SBATCH --job-name=dgs-fetch-test
#SBATCH --time=24:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
# Explicit memory: see extract_batch.sbatch.
# #SBATCH --mem=8G
#SBATCH --output=slurm-fetch-test-%j.log
# Site-specific flags are filled in by the site notes; leave them commented until then.
# #SBATCH --partition=<partition>
# #SBATCH --account=<project-account>
set -euo pipefail
umask 002

: "${DGS_SETTINGS:?set DGS_SETTINGS to the settings TOML file}"
export DGS_SETTINGS
cd "${SLURM_SUBMIT_DIR:-.}"

STAGING="$(uv run --frozen python -c 'from desi_galaxy_spectra.settings import load_settings; print(load_settings().staging)')"
TEST_ROOT="$STAGING/fetch-test-${SLURM_JOB_ID:-manual}"
mkdir -p "$TEST_ROOT"
echo 21852 > "$TEST_ROOT/pixels.txt"

echo "fetch test on $(hostname) at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
START="$(date +%s)"
uv run --frozen dgs fetch --pixels "$TEST_ROOT/pixels.txt" --raw-root "$TEST_ROOT"
END="$(date +%s)"

BYTES="$(du -sb "$TEST_ROOT" | cut -f1)"
echo "bytes: $BYTES"
echo "seconds: $((END - START))"
rm -rf "$TEST_ROOT"
