import hashlib
import json

import pytest

from desi_galaxy_spectra import config, dryrun
from desi_galaxy_spectra.fetch import FetchError, Fetcher, parse_sha256sum


class Resp:
    def __init__(self, status=200, body=b"", headers=None):
        self.status_code, self.body, self.headers = status, body, headers or {}

    def iter_content(self, n):
        for i in range(0, len(self.body), n):
            yield self.body[i:i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeSession:
    """Serves {url: bytes}; records calls; optionally fails the first N GETs mid-way."""

    def __init__(self, files, fail_gets=0, honour_range=True, missing=()):
        self.files, self.fail_gets, self.honour_range = files, fail_gets, honour_range
        self.missing = set(missing)
        self.calls = []

    def head(self, url, **kw):
        self.calls.append(("HEAD", url))
        if url in self.missing or url not in self.files:
            return Resp(404)
        return Resp(200, headers={"Content-Length": str(len(self.files[url]))})

    def get(self, url, headers=None, **kw):
        self.calls.append(("GET", url, (headers or {}).get("Range")))
        if self.fail_gets > 0:
            self.fail_gets -= 1
            raise ConnectionError("boom")
        body = self.files[url]
        rng = (headers or {}).get("Range")
        if rng and self.honour_range:
            start = int(rng.split("=")[1].split("-")[0])
            return Resp(206, body[start:])
        return Resp(200, body)


def pixel_files(pix, tamper=False):
    files = {}
    for prog, kind, fn, url in config.pixel_urls(pix):
        if kind != "sha256sum":
            files[url] = (f"{fn}-payload-" * 50).encode()
    for prog in config.PROGRAMS:
        lines = []
        for kind, fn in (("coadd", config.coadd_name(pix, prog)),
                         ("redrock", config.redrock_name(pix, prog))):
            body = files[f"{config.remote_dir(pix, prog)}/{fn}"]
            lines.append(f"{hashlib.sha256(body).hexdigest() if not tamper else '0' * 64}  {fn}")
        lines.append("ab" * 32 + "  some-other-file.fits")
        files[f"{config.remote_dir(pix, prog)}/{config.sha_name(pix, prog)}"] = \
            ("\n".join(lines) + "\n").encode()
    return files


def test_urls_and_layout():
    u = {(p, k): url for p, k, fn, url in config.pixel_urls(21852)}
    assert u[("bright", "coadd")].endswith(
        "/healpix/main/bright/218/21852/coadd-main-bright-21852.fits")
    assert u[("dark", "sha256sum")].endswith(
        "/main/dark/218/21852/redux_iron_healpix_main_dark_218_21852.sha256sum")
    assert str(config.raw_dir("/r", 21852, "dark")) == "/r/dark/218/21852"


def test_fetch_pixel_ok_and_resumable(tmp_path):
    files = pixel_files(21852)
    sess = FakeSession(files, fail_gets=2)
    f = Fetcher(sess, sleep=lambda s: None)
    dirs = f.fetch_pixel(21852, tmp_path, log=lambda m: None)
    for prog, d in dirs.items():
        assert (d / config.SHA_MARKER).exists()
        assert (d / config.coadd_name(21852, prog)).exists()
    n = len([c for c in sess.calls if c[0] == "GET"])
    f.fetch_pixel(21852, tmp_path, log=lambda m: None)         # second run: nothing fetched
    assert len([c for c in sess.calls if c[0] == "GET"]) == n


def test_download_resumes_from_part(tmp_path):
    url = "http://x/f.bin"
    body = bytes(range(256)) * 100
    sess = FakeSession({url: body})
    dest = tmp_path / "f.bin"
    (tmp_path / "f.bin.part").write_bytes(body[:1000])
    Fetcher(sess, sleep=lambda s: None).download(url, dest, expected_size=len(body))
    assert dest.read_bytes() == body
    assert sess.calls[-1][2] == "bytes=1000-"
    # server ignoring Range restarts cleanly
    sess2 = FakeSession({url: body}, honour_range=False)
    d2 = tmp_path / "g.bin"
    (tmp_path / "g.bin.part").write_bytes(body[:1000])
    Fetcher(sess2, sleep=lambda s: None).download(url, d2, expected_size=len(body))
    assert d2.read_bytes() == body


def test_sha_mismatch_and_missing_entry(tmp_path):
    f = Fetcher(FakeSession(pixel_files(7, tamper=True)), sleep=lambda s: None)
    with pytest.raises(FetchError, match="SHA256 mismatch"):
        f.fetch_pixel(7, tmp_path, log=lambda m: None)
    assert not (config.raw_dir(tmp_path, 7, "bright") / config.SHA_MARKER).exists()
    assert parse_sha256sum("abc  *dir/x.fits\n") == {"x.fits": "abc"}


def test_fetch_404(tmp_path):
    files = pixel_files(8)
    miss = [u for u in files if "redrock" in u and "dark" in u]
    f = Fetcher(FakeSession(files, missing=miss), sleep=lambda s: None)
    with pytest.raises(FetchError, match="HEAD status 404"):
        f.fetch_pixel(8, tmp_path, log=lambda m: None)


def test_dry_run_mocked_head(tmp_path):
    pixels = [21852, 21853, 21854]
    files = {}
    for p in pixels:
        files.update(pixel_files(p))
    missing_url = f"{config.remote_dir(21853, 'dark')}/{config.redrock_name(21853, 'dark')}"
    sess = FakeSession(files, missing=[missing_url])
    meas = tmp_path / "gate_measurements.json"
    meas.write_text(json.dumps({"seconds_per_pixel_pair_total": 100.0,
                                "bytes_per_object_with_resolution": 400000.0,
                                "bytes_per_object_without_resolution": 100000.0}))
    out = tmp_path / "out"
    s = dryrun.run(pixels, tmp_path, tmp_path / "shards", tmp_path / "stage", out_dir=out,
                   fetcher=Fetcher(sess), measurements_path=meas, workers=2,
                   log=lambda m: None)
    assert all(c[0] == "HEAD" for c in sess.calls)            # no downloads
    assert len(sess.calls) == 6 * len(pixels)
    assert s["n_missing_files"] == 1 and "21853 dark redrock" in s["missing"][0]
    assert s["download_bytes_total"] == sum(
        len(v) for u, v in files.items() if u != missing_url)
    assert s["projected_seconds_serial"] == 300.0
    assert s["projected_seconds_wall_assuming_linear_scaling"] == 150.0
    assert (out / "per_pixel.csv").exists() and (out / "projected_counts_class_chunk.csv").exists()
    assert s["warnings"]                                       # not the drawn-100 list
    rows = (out / "per_pixel.csv").read_text().splitlines()
    assert len(rows) == 4


def test_dry_run_requires_gate_measurements(tmp_path):
    with pytest.raises(dryrun.DryRunError, match="not found"):
        dryrun.load_measurements(tmp_path / "nope.json")
    bad = tmp_path / "m.json"
    bad.write_text("{}")
    with pytest.raises(dryrun.DryRunError, match="lacks"):
        dryrun.load_measurements(bad)
