"""Constants for the DESI DR1 kit: URLs, file names, default paths.

``RESOLUTION_STORAGE_DTYPE`` is set here and nowhere else.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

KIT_VERSION = "1.0.0"

# dtype of the resolution dataset in chunk files (float16 is gated elsewhere, not adopted here)
RESOLUTION_STORAGE_DTYPE = "f4"

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCKFILE = REPO_ROOT / "requirements.lock.txt"
PIXEL_LISTS = REPO_ROOT / "pixel_lists"
COUNTS_CSV = PIXEL_LISTS / "counts_class_chunk.csv"
PIXEL_TABLE_CSV = PIXEL_LISTS / "pixel_photsys_table.csv"
GATE_DIR = REPO_ROOT / "reference" / "provenance" / "kit-gate"
GATE_MEASUREMENTS = GATE_DIR / "gate_measurements.json"
DRY_RUN_DIR = REPO_ROOT / "reference" / "provenance" / "dry-run-100"

STAGING_MARGIN_BYTES = 5 * 10**9      # headroom above the download size for temp files

SURVEY = "main"
PROGRAMS = ("bright", "dark")
BASE_URL = "https://data.desi.lbl.gov/public/dr1/spectro/redux/iron/healpix"
SHA_MARKER = "SHA256_OK"

HTTP_RETRIES = 5
HTTP_TIMEOUT_S = 120
HTTP_CHUNK = 1 << 20


def pixgroup(pix: int) -> int:
    return int(pix) // 100


def coadd_name(pix: int, prog: str) -> str:
    return f"coadd-{SURVEY}-{prog}-{int(pix)}.fits"


def redrock_name(pix: int, prog: str) -> str:
    return f"redrock-{SURVEY}-{prog}-{int(pix)}.fits"


def sha_name(pix: int, prog: str) -> str:
    return f"redux_iron_healpix_{SURVEY}_{prog}_{pixgroup(pix)}_{int(pix)}.sha256sum"


def remote_dir(pix: int, prog: str) -> str:
    return f"{BASE_URL}/{SURVEY}/{prog}/{pixgroup(pix)}/{int(pix)}"


def pixel_urls(pix: int):
    """[(prog, kind, filename, url)] for every file the kit needs for one pixel."""
    out = []
    for prog in PROGRAMS:
        for kind, fn in (("coadd", coadd_name(pix, prog)),
                         ("redrock", redrock_name(pix, prog)),
                         ("sha256sum", sha_name(pix, prog))):
            out.append((prog, kind, fn, f"{remote_dir(pix, prog)}/{fn}"))
    return out


def raw_dir(root: Path, pix: int, prog: str) -> Path:
    """<root>/{bright,dark}/<pix//100>/<pix>/"""
    return Path(root) / prog / str(pixgroup(pix)) / str(int(pix))


def shard_name(pix: int) -> str:
    return f"healpix_{int(pix)}.h5"


def lockfile_sha256() -> str:
    return hashlib.sha256(LOCKFILE.read_bytes()).hexdigest()


def read_pixel_list(path) -> list[int]:
    pix = []
    for line in Path(path).read_text().splitlines():
        line = line.split("#")[0].strip()
        if line:
            pix.append(int(line))
    if len(set(pix)) != len(pix):
        raise ValueError(f"duplicate pixels in {path}")
    return pix
