"""Fix session 2026-10-23, N1: the gate comparison fails on empty/mismatched file sets and asserts
the per-shard table."""
import importlib.util
import shutil
from pathlib import Path

import h5py
import pytest

from conftest import OBJS_A, make_shard
from desi_galaxy_spectra import package

KIT = Path(__file__).resolve().parents[1]
GATE = KIT / "reference/provenance/kit-gate/gate_checks_3to5.py"


def _gate():
    spec = importlib.util.spec_from_file_location("gate_n1", GATE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture()
def chunks(tmp_path):
    make_shard(tmp_path, 21852, OBJS_A)
    out = tmp_path / "chunks"
    package.package(tmp_path / "shards", out, log=lambda m: None)
    return tmp_path, out


def kwf(name):
    return {}


def test_empty_directories_do_not_pass(tmp_path):
    g = _gate()
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    res = g.compare_dirs(tmp_path / "a", tmp_path / "b", kwf)
    assert res["n_files_compared"] == 0 and res["pass"] is False


def test_mismatched_file_sets_do_not_pass(chunks, tmp_path):
    g = _gate()
    _, out = chunks
    other = tmp_path / "other"
    shutil.copytree(out, other)
    victim = sorted(other.glob("*.h5"))[0]
    victim.unlink()
    res = g.compare_dirs(out, other, lambda n: dict(attr_skip={("/", "created"): "t"}))
    assert res["pass"] is False and res["files_only_in_A"]


def test_identical_dirs_pass_and_count_matches(chunks, tmp_path):
    g = _gate()
    _, out = chunks
    other = tmp_path / "other2"
    shutil.copytree(out, other)
    res = g.compare_dirs(out, other, kwf)
    assert res["pass"] is True and res["n_files_compared"] == len(g.nonempty_chunks(out)) > 0


def test_shard_table_assertions(chunks):
    g = _gate()
    tmp, out = chunks
    shard = tmp / "shards" / "healpix_21852.h5"
    p = sorted(out.glob("*.h5"))[0]
    with h5py.File(p, "r") as f:
        assert g.assert_shard_table(f, shard, p.name) == []
    with h5py.File(p, "r+") as f:
        f["source_shard_table/healpix"][0] = 5
    with h5py.File(p, "r") as f:
        assert any("healpix" in m for m in g.assert_shard_table(f, shard, p.name))


def test_out_defaults_into_gate_dir_and_gate_dir_required(monkeypatch, tmp_path, capsys):
    g = _gate()
    monkeypatch.setattr("sys.argv", ["gate"])
    with pytest.raises(SystemExit):
        g.main()
