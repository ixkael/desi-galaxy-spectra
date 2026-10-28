"""Fixed batch membership: 84 numbered batches (000..083) plus the 22-pixel reference batch."""
import csv
import json
import subprocess
import sys

from desi_galaxy_spectra.batches import BATCH_DIR, batch_ids, read_batch
from desi_galaxy_spectra.config import PIXEL_LISTS, PIXEL_TABLE_CSV, REPO_ROOT, read_pixel_list


def _load_table():
    with open(PIXEL_TABLE_CSV, newline="") as fh:
        return list(csv.DictReader(fh))


TABLE = _load_table()
ELIGIBLE = {int(r["healpix"]) for r in TABLE if r["eligible"] == "1"}


def test_batch_ids():
    ids = batch_ids()
    assert ids[:3] == ["000", "001", "002"] and ids[-2:] == ["083", "ref22"] and len(ids) == 85
    assert batch_ids(include_ref22=False) == ids[:-1]


def test_every_eligible_pixel_in_exactly_one_numbered_batch():
    seen = [p for b in batch_ids(include_ref22=False) for p in read_batch(b)]
    assert len(seen) == len(set(seen)) == 8412 and set(seen) == ELIGIBLE


def test_batch_sizes():
    sizes = [len(read_batch(b)) for b in batch_ids(include_ref22=False)]
    assert sizes[0] == 100 and sizes[1:].count(101) == 12 and sizes[1:].count(100) == 71


def test_batch000_is_drawn100_and_ref22_is_the_22():
    assert read_batch("000") == sorted(read_pixel_list(PIXEL_LISTS / "dr1_decals_random100.txt"))
    assert len(read_batch("ref22")) == 22 and set(read_batch("ref22")).isdisjoint(ELIGIBLE)


def test_batches_are_sorted_and_files_present():
    for b in batch_ids():
        pix = read_batch(b)
        assert pix == sorted(pix)
        assert (BATCH_DIR / f"batch_{b}.txt").read_text().startswith("# seed 20261028; rule: ")


def test_projected_clean_totals():
    idx = json.loads((BATCH_DIR / "index.json").read_text())
    assert idx["seed"] == 20261028
    assert idx["batches"]["000"]["projected_clean"] == 97553
    assert idx["batches"]["ref22"]["projected_clean"] == 44219
    assert sum(idx["batches"][b]["projected_clean"] for b in batch_ids(False)) == 8694127
    assert min(idx["ref22_photsys_S_fraction"].values()) >= 0.95


def test_build_is_reproducible(tmp_path):
    subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "build_batches.py"),
                    "--seed", "20261028", "--out-dir", str(tmp_path)], check=True)
    for b in batch_ids():
        name = f"batch_{b}.txt"
        assert (tmp_path / name).read_bytes() == (BATCH_DIR / name).read_bytes()
    assert (tmp_path / "index.json").read_bytes() == (BATCH_DIR / "index.json").read_bytes()
