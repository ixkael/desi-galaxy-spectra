"""Settings file: TOML read, $VAR and ~ expansion, lookup order, unknown keys refused."""
from pathlib import Path

import pytest

from desi_galaxy_spectra.settings import Settings, load_settings

BASE = 'raw_root="/a/raw"\nshard_root="/a/shards"\nbatch_root="/a/batches"\n'


def test_load_settings_reads_toml_and_expands(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", "/node/tmp")
    p = tmp_path / "s.toml"
    p.write_text(BASE + 'staging="$TMPDIR"\nworkers=64\n')
    s = load_settings(p)
    assert s.staging == Path("/node/tmp") and s.workers == 64 and s.keep_raw_after_verify is True
    assert s.raw_root == Path("/a/raw") and s.batch_root == Path("/a/batches")


def test_load_settings_defaults(tmp_path):
    p = tmp_path / "s.toml"
    p.write_text(BASE + 'staging="/stage"\n')
    s = load_settings(p)
    assert s.workers == 1
    assert s.keep_raw_after_verify is True and s.keep_shards_after_verify is True
    assert s.with_resolution is True and s.reference_root is None and s.group is None


def test_load_settings_expands_tilde_and_optional_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", "/home/someone")
    p = tmp_path / "s.toml"
    p.write_text(
        'raw_root="~/raw"\nshard_root="~/sh"\nbatch_root="~/b"\nstaging="~/st"\n'
        'reference_root="~/ref"\ngroup="desi"\nwith_resolution=false\n'
    )
    s = load_settings(p)
    assert s.raw_root == Path("/home/someone/raw")
    assert s.reference_root == Path("/home/someone/ref")
    assert s.group == "desi" and s.with_resolution is False


def test_load_settings_is_frozen(tmp_path):
    p = tmp_path / "s.toml"
    p.write_text(BASE + 'staging="/stage"\n')
    s = load_settings(p)
    assert isinstance(s, Settings)
    with pytest.raises(AttributeError):
        s.workers = 2


def test_load_settings_missing_names_both_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DGS_SETTINGS", raising=False)
    with pytest.raises(FileNotFoundError, match=r"DGS_SETTINGS.*settings\.toml"):
        load_settings()


def test_load_settings_uses_env_var_then_cwd(tmp_path, monkeypatch):
    (tmp_path / "settings.toml").write_text(BASE + 'staging="/cwd"\n')
    other = tmp_path / "other.toml"
    other.write_text(BASE + 'staging="/env"\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DGS_SETTINGS", raising=False)
    assert load_settings().staging == Path("/cwd")
    monkeypatch.setenv("DGS_SETTINGS", str(other))
    assert load_settings().staging == Path("/env")


def test_load_settings_rejects_unknown_key(tmp_path):
    p = tmp_path / "s.toml"
    p.write_text('raw_rot="/a/raw"\n' + BASE + 'staging="/stage"\n')
    with pytest.raises(ValueError, match="raw_rot"):
        load_settings(p)
