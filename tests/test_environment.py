"""The installed environment equals the released pins, and the exported
requirements file stays in step with uv.lock."""
import importlib.metadata
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_installed_versions_equal_pins():
    pins = dict(
        line.split("==")
        for line in (ROOT / "reference/pins_v0.2.0.txt").read_text().split()
    )
    assert len(pins) == 37
    for name, ver in pins.items():
        assert importlib.metadata.version(name) == ver, name


def test_requirements_export_matches_uv_lock():
    out = subprocess.run(
        [
            "uv", "export", "--frozen", "--no-hashes", "--no-emit-project",
            "--no-dev", "--no-header", "--project", str(ROOT),
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    ).stdout
    assert out == (ROOT / "requirements.lock.txt").read_text()
