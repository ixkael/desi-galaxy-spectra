"""The Slurm templates take only the settings file and the batch id, and set umask 002."""
import re
import subprocess
from pathlib import Path

import pytest

from desi_galaxy_spectra.settings import load_settings

ROOT = Path(__file__).resolve().parents[1]
SLURM = ROOT / "slurm"
NAMES = ["extract_batch", "package_verify_batch", "extract_pixel_array", "fetch_test"]
TEMPLATES = [SLURM / f"{n}.sbatch" for n in NAMES]

# an absolute path: a slash that starts a word (after space, quote, '=', '(' or at line start)
ABS_PATH = re.compile(r"""(?:^|[\s"'=(])/[A-Za-z0-9_.-]""")
# inputs the templates may read: the settings file and the batch id (plus what Slurm provides)
ALLOWED_DGS = {"DGS_SETTINGS", "DGS_BATCH"}


def _text(p):
    return p.read_text()


def test_all_templates_present():
    assert all(p.is_file() for p in TEMPLATES), [p.name for p in TEMPLATES if not p.is_file()]


@pytest.mark.parametrize("path", TEMPLATES, ids=NAMES)
def test_slurm_templates_take_only_settings_and_batch(path):
    text = _text(path)
    lines = text.splitlines()
    assert lines[0] == "#!/bin/bash"
    # no absolute path anywhere (code or comments) other than placeholders such as <nas>/...
    for n, line in enumerate(lines[1:], start=2):
        assert not ABS_PATH.search(line), f"{path.name}:{n}: absolute path: {line}"
    # the only DGS_* inputs are the settings file and the batch id
    assert set(re.findall(r"\bDGS_[A-Z_]+\b", text)) <= ALLOWED_DGS
    assert "DGS_SETTINGS" in text
    # the settings file must be set, or the job stops at once
    assert re.search(r'^: "\$\{DGS_SETTINGS:\?', text, re.M)
    # strict shell, group-writable output
    assert re.search(r"^set -euo pipefail$", text, re.M)
    assert re.search(r"^umask 002$", text, re.M)
    assert re.search(r"^#SBATCH --time=24:00:00$", text, re.M)


@pytest.mark.parametrize("path", TEMPLATES, ids=NAMES)
def test_site_flags_are_commented_placeholders(path):
    for line in _text(path).splitlines():
        if re.search(r"--(partition|account|gres|gpus|qos|mem)\b", line):
            assert re.match(r"#\s+#SBATCH ", line), f"{path.name}: live site flag: {line}"


@pytest.mark.parametrize("path", TEMPLATES, ids=NAMES)
def test_syntax(path):
    subprocess.run(["bash", "-n", str(path)], check=True)


def test_stage_commands():
    ex = _text(SLURM / "extract_batch.sbatch")
    assert re.search(r"dgs run --batch \"\$ID\" --claim --until extracted", ex)
    assert "--workers" not in ex          # the worker count comes from the settings
    pv = _text(SLURM / "package_verify_batch.sbatch")
    assert re.search(r"dgs run --batch \"\$ID\" --until verified", pv)
    assert "--claim" not in pv
    fx = _text(SLURM / "fetch_test.sbatch")
    assert "dgs fetch" in fx and "--pixels" in fx and "--raw-root" in fx
    pa = _text(SLURM / "extract_pixel_array.sbatch")
    assert "dgs extract" in pa and "--pixel" in pa


@pytest.mark.parametrize("name", ["extract_batch", "package_verify_batch"])
def test_batch_log_goes_beside_the_batch(name):
    text = _text(SLURM / f"{name}.sbatch")
    assert "batch_$ID/slurm-" in text.replace("${ID}", "$ID") and ".log" in text


def test_example_settings_load_with_64_workers(monkeypatch, tmp_path):
    monkeypatch.setenv("DGS_TREE", str(tmp_path))
    s = load_settings(ROOT / "settings.example-isambard.toml")
    assert s.workers == 64 and s.with_resolution is True
    assert s.raw_root == tmp_path / "raw" and s.batch_root == tmp_path / "batches"
    assert s.keep_raw_after_verify is True
