"""The single-programme guard in `extract_to_temp` raises only when a BGS row is at stake.

Change note `plans/2026-10-30-dgs-shard-bright-guard-change-note.md` (pop-cosmos_DESI),
PI-approved 2026-10-30: the hazard is "a BGS-classed row exists *and* no bright row was
extracted", not "a programme contributed no rows".  `check_bgs_kept` is unchanged and still
turns an actual drop by the frozen extractor into a hard error.
"""
import h5py
import pytest

from desi_galaxy_spectra import shard


def _rows(specs):
    """specs: (program, class) pairs."""
    return [{"program": p, "class": c} for p, c in specs]


def _fake_write(skipped=False, seen=None):
    def write(rows, path, n_coadds, log, programs_processed):
        if seen is not None:
            seen["programs_processed"] = sorted(programs_processed)
        with h5py.File(path, "w") as h:
            meta = h.create_group("metadata")
            meta.attrs["bgs_group_skipped"] = skipped
            meta.attrs["programs_processed"] = "|".join(sorted(programs_processed))
            meta.attrs["has_bright_program"] = "bright" in programs_processed
    return write


def test_dark_only_without_bgs_rows_extracts(tmp_path, monkeypatch):
    """Pixel 17496's case: dark-only rows, none BGS-classed — nothing would be dropped."""
    seen = {}
    monkeypatch.setattr(shard.ext, "collect_rows",
                        lambda *a, **k: _rows([("dark", "ELG"), ("dark", "LRG")]))
    monkeypatch.setattr(shard.ext, "write_hdf5", _fake_write(seen=seen))
    temp = tmp_path / "t.h5"
    assert shard.extract_to_temp([tmp_path / "a.fits"], tmp_path, temp) == 2
    assert seen["programs_processed"] == ["dark"]
    with h5py.File(temp, "r") as h:
        assert bool(h["metadata"].attrs["has_bright_program"]) is False
        assert bool(h["metadata"].attrs["bgs_group_skipped"]) is False


def test_dark_only_with_a_bgs_row_still_raises(tmp_path, monkeypatch):
    """The case the guard exists for: a BGS-classed row the extractor would drop."""
    monkeypatch.setattr(shard.ext, "collect_rows",
                        lambda *a, **k: _rows([("dark", "ELG"), ("dark", "BGS")]))
    monkeypatch.setattr(shard.ext, "write_hdf5", _fake_write())
    with pytest.raises(shard.ShardError, match="1 BGS-from-dark rows"):
        shard.extract_to_temp([tmp_path / "a.fits"], tmp_path, tmp_path / "t.h5")


def test_bright_only_extracts(tmp_path, monkeypatch):
    """No dark programme: BGS rows are real BGS, nothing is dropped."""
    monkeypatch.setattr(shard.ext, "collect_rows",
                        lambda *a, **k: _rows([("bright", "BGS"), ("bright", "BGS")]))
    monkeypatch.setattr(shard.ext, "write_hdf5", _fake_write())
    assert shard.extract_to_temp([tmp_path / "a.fits"], tmp_path, tmp_path / "t.h5") == 2


def test_both_programmes_unchanged(tmp_path, monkeypatch):
    """The ordinary pixel: unchanged behaviour, and a drop is still a hard error."""
    rows = _rows([("bright", "BGS"), ("dark", "ELG"), ("dark", "LRG")])
    monkeypatch.setattr(shard.ext, "collect_rows", lambda *a, **k: rows)
    monkeypatch.setattr(shard.ext, "write_hdf5", _fake_write())
    assert shard.extract_to_temp([tmp_path / "a.fits"], tmp_path, tmp_path / "t.h5") == 3
    monkeypatch.setattr(shard.ext, "write_hdf5", _fake_write(skipped=True))
    with pytest.raises(shard.ShardError, match="bgs_group_skipped"):
        shard.extract_to_temp([tmp_path / "a.fits"], tmp_path, tmp_path / "t2.h5")
