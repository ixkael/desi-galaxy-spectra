"""Guards on the frozen extractor and packager and on the paths stub."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def test_frozen_sources_unchanged():
    ref = json.loads((ROOT / "reference/frozen_sources.json").read_text())
    for name, sha in ref["files"].items():
        assert sha256_file(ROOT / "desi_galaxy_spectra/frozen" / name) == sha


def test_stub_paths_are_none_and_private_names_absent():
    from desi_galaxy_spectra.frozen.popfit import paths

    assert paths.DESI_DR1_HEALPIX is None and paths.FINAL_DESI_EXTRACTION is None
    assert {n for n in dir(paths) if not n.startswith("_")} == {
        "DESI_DR1_HEALPIX",
        "FINAL_DESI_EXTRACTION",
    }


def test_extractor_imports_with_stub():
    from desi_galaxy_spectra.frozen import extract_desi_dr1_final as ext

    assert callable(ext.collect_rows) and callable(ext.write_hdf5)
