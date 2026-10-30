"""`dgs fetch --base-url` / `--jobs`: a mirror of the same layout, and parallel pixels.

Added 2026-10-30 with the PIC-mirror download route (change note
pop-cosmos_DESI/plans/2026-10-30-dgs-fetch-base-url-change-note.md).
"""
import hashlib

import pytest

from desi_galaxy_spectra import cli, config
from desi_galaxy_spectra.fetch import FetchError, Fetcher

from test_fetch_dryrun import FakeSession, Resp

MIRROR = "https://mirror.example/data/public/DESI/DR1/spectro/redux/iron/healpix"


def mirror_files(pix, base_url=MIRROR, tamper=False):
    """Same bodies as `pixel_files`, served under `base_url` instead of the DESI host."""
    files = {}
    for prog, kind, fn, url in config.pixel_urls(pix, base_url):
        if kind != "sha256sum":
            files[url] = (f"{fn}-payload-" * 50).encode()
    for prog in config.PROGRAMS:
        lines = []
        for kind, fn in (("coadd", config.coadd_name(pix, prog)),
                         ("redrock", config.redrock_name(pix, prog))):
            body = files[f"{config.remote_dir(pix, prog, base_url)}/{fn}"]
            lines.append(f"{hashlib.sha256(body).hexdigest() if not tamper else '0' * 64}  {fn}")
        files[f"{config.remote_dir(pix, prog, base_url)}/{config.sha_name(pix, prog)}"] = \
            ("\n".join(lines) + "\n").encode()
    return files


def test_base_url_changes_only_the_host():
    """Every path below the host is byte-identical to the default route."""
    default = config.pixel_urls(21852)
    mirrored = config.pixel_urls(21852, MIRROR)
    assert [(p, k, fn) for p, k, fn, _ in mirrored] == [(p, k, fn) for p, k, fn, _ in default]
    for (_, _, _, d), (_, _, _, m) in zip(default, mirrored):
        assert d.startswith(config.BASE_URL) and m.startswith(MIRROR)
        assert d[len(config.BASE_URL):] == m[len(MIRROR):]
    # a trailing slash on the mirror must not double up
    assert config.remote_dir(10, "dark", MIRROR + "/") == config.remote_dir(10, "dark", MIRROR)


def test_default_base_url_is_unchanged():
    assert all(u.startswith(config.BASE_URL) for *_, u in config.pixel_urls(21852))
    assert all(u.startswith(config.BASE_URL) for *_, u in config.pixel_urls(21852, None))


def test_fetch_pixel_from_mirror(tmp_path):
    files = mirror_files(21852)
    sess = FakeSession(files)
    Fetcher(sess, base_url=MIRROR).fetch_pixel(21852, tmp_path, log=lambda m: None)
    assert all(u.startswith(MIRROR) for c in sess.calls for u in [c[1]])
    for prog in config.PROGRAMS:
        d = config.raw_dir(tmp_path, 21852, prog)
        assert (d / config.SHA_MARKER).exists()
        assert (d / config.coadd_name(21852, prog)).exists()


class NoLengthSession(FakeSession):
    """A mirror that answers HEAD with Content-Length 0, as the PIC copy does."""

    def head(self, url, **kw):
        self.calls.append(("HEAD", url))
        if url in self.missing or url not in self.files:
            return Resp(404)
        return Resp(200, headers={"Content-Length": "0"})


def test_zero_content_length_still_downloads_and_verifies(tmp_path):
    files = mirror_files(21852)
    sess = NoLengthSession(files)
    Fetcher(sess, base_url=MIRROR).fetch_pixel(21852, tmp_path, log=lambda m: None)
    for prog in config.PROGRAMS:
        d = config.raw_dir(tmp_path, 21852, prog)
        assert (d / config.SHA_MARKER).exists()
        got = (d / config.coadd_name(21852, prog)).read_bytes()
        assert got == files[f"{config.remote_dir(21852, prog, MIRROR)}/"
                            f"{config.coadd_name(21852, prog)}"]


def test_zero_content_length_tampered_file_still_fails(tmp_path):
    """With no size to check, sha256 is the only gate - it must still catch a bad file."""
    files = mirror_files(21852, tamper=True)
    sess = NoLengthSession(files)
    with pytest.raises(FetchError, match="SHA256 mismatch"):
        Fetcher(sess, base_url=MIRROR).fetch_pixel(21852, tmp_path, log=lambda m: None)
    d = config.raw_dir(tmp_path, 21852, "bright")
    assert not (d / config.coadd_name(21852, "bright")).exists()   # removed, so a rerun refetches
    assert not (d / config.SHA_MARKER).exists()


def test_cmd_fetch_jobs_fetches_every_pixel(tmp_path, monkeypatch):
    pixels = [21852, 21853, 21854, 21855]
    files = {}
    for p in pixels:
        files.update(mirror_files(p))
    sessions = []

    def fake_fetcher(*a, **kw):
        s = FakeSession(files)
        sessions.append(s)
        return Fetcher(s, base_url=kw.get("base_url"))

    monkeypatch.setattr("desi_galaxy_spectra.fetch.Fetcher", fake_fetcher)
    plist = tmp_path / "pixels.txt"
    plist.write_text("\n".join(str(p) for p in pixels) + "\n")

    args = type("A", (), {"pixels": str(plist), "pixel": None, "raw_root": str(tmp_path / "raw"),
                          "base_url": MIRROR, "jobs": 4, "settings": None})()
    assert cli.cmd_fetch(args) == 0
    for p in pixels:
        for prog in config.PROGRAMS:
            assert (config.raw_dir(tmp_path / "raw", p, prog) / config.SHA_MARKER).exists()
    assert len(sessions) > 1          # one session per worker, not one shared across threads


def test_cmd_fetch_jobs_propagates_a_failure(tmp_path, monkeypatch):
    pixels = [21852, 21853]
    files = mirror_files(21852)       # 21853 is absent -> HEAD 404

    monkeypatch.setattr("desi_galaxy_spectra.fetch.Fetcher",
                        lambda *a, **kw: Fetcher(FakeSession(files), base_url=kw.get("base_url")))
    plist = tmp_path / "pixels.txt"
    plist.write_text("\n".join(str(p) for p in pixels) + "\n")
    args = type("A", (), {"pixels": str(plist), "pixel": None, "raw_root": str(tmp_path / "raw"),
                          "base_url": MIRROR, "jobs": 2, "settings": None})()
    with pytest.raises(FetchError, match="HEAD status 404"):
        cli.cmd_fetch(args)
