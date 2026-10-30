"""HTTPS download of the per-pixel DESI DR1 files, checksum verification, resumable."""
from __future__ import annotations

import hashlib
import os
import shutil
import time
from pathlib import Path

from . import config


class FetchError(RuntimeError):
    pass


def sha256_file(path, block=1 << 24) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(block)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def parse_sha256sum(text: str) -> dict:
    """{filename: hexdigest} from `sha256sum` output (binary-mode '*' prefix tolerated)."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        digest, _, name = line.partition(" ")
        name = name.strip().lstrip("*")
        out[os.path.basename(name)] = digest.lower()
    return out


def free_bytes(path) -> int:
    """Free bytes on the filesystem holding `path` (nearest existing ancestor)."""
    p = Path(path).absolute()
    while not p.exists():
        p = p.parent
    return shutil.disk_usage(p).free


class Fetcher:
    """Thin wrapper over a requests-like session (injectable for tests)."""

    def __init__(self, session=None, retries=config.HTTP_RETRIES, sleep=time.sleep,
                 timeout=config.HTTP_TIMEOUT_S, base_url=None):
        if session is None:
            import requests
            session = requests.Session()
        self.session = session
        self.retries = retries
        self.sleep = sleep
        self.timeout = timeout
        self.base_url = base_url        # None = config.BASE_URL (the DESI host)

    # ------------------------------------------------------------------ HEAD
    def head(self, url):
        """(status_code, content_length or None).

        Transport errors, HTTP 5xx and HTTP 429 are retried with exponential backoff and raise
        FetchError when the retries are used up.  Any other status (200, 404, 403, ...) is
        returned at once, so a missing file or a refusal is never retried.
        """
        last = None
        for k in range(self.retries):
            try:
                r = self.session.head(url, allow_redirects=True, timeout=self.timeout)
            except Exception as exc:       # noqa: BLE001 - retry on any transport error
                last = exc
                self.sleep(min(2 ** k, 30))
                continue
            if r.status_code >= 500 or r.status_code == 429:
                last = f"HTTP {r.status_code}"
                self.sleep(min(2 ** k, 30))
                continue
            n = r.headers.get("Content-Length")
            return r.status_code, (int(n) if n is not None else None)
        raise FetchError(f"HEAD failed for {url}: {last}")

    # -------------------------------------------------------------- download
    def download(self, url, dest, expected_size=None):
        """Resumable download to `dest` (via dest.part).  Returns bytes on disk."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        if dest.exists() and (expected_size is None or dest.stat().st_size == expected_size):
            return dest.stat().st_size
        last = None
        for k in range(self.retries):
            try:
                have = part.stat().st_size if part.exists() else 0
                headers = {"Range": f"bytes={have}-"} if have else {}
                with self.session.get(url, headers=headers, stream=True,
                                      timeout=self.timeout) as r:
                    if r.status_code == 416:            # already complete
                        break
                    if r.status_code == 200 and have:   # server ignored Range: restart
                        have = 0
                    elif r.status_code not in (200, 206):
                        raise FetchError(f"GET {url} -> HTTP {r.status_code}")
                    mode = "ab" if have else "wb"
                    with open(part, mode) as f:
                        for blk in r.iter_content(config.HTTP_CHUNK):
                            if blk:
                                f.write(blk)
                size = part.stat().st_size
                if expected_size is not None and size != expected_size:
                    if size > expected_size:
                        part.unlink()
                    raise FetchError(f"{url}: got {size} B, expected {expected_size} B")
                break
            except Exception as exc:       # noqa: BLE001
                last = exc
                self.sleep(min(2 ** k, 30))
        else:
            raise FetchError(f"download failed for {url}: {last}")
        os.replace(part, dest)
        return dest.stat().st_size

    # ----------------------------------------------------------------- pixel
    def fetch_pixel(self, pix, root, log=print, progs=None):
        """Download + sha256-verify all files of one pixel under `root` (resumable).
        Returns {prog: dir}.  Verification is restricted to the fetched coadd/redrock files.
        `progs` restricts the fetch to some programmes (default: both).
        """
        dirs = {}
        for prog in (progs or config.PROGRAMS):
            d = config.raw_dir(root, pix, prog)
            d.mkdir(parents=True, exist_ok=True)
            dirs[prog] = d
            marker = d / config.SHA_MARKER
            names = {k: fn for p, k, fn, _ in config.pixel_urls(pix) if p == prog}
            urls = {k: u for p, k, _, u in config.pixel_urls(pix, self.base_url) if p == prog}
            if marker.exists() and all((d / names[k]).exists() for k in ("coadd", "redrock")):
                continue
            for kind in ("sha256sum", "redrock", "coadd"):
                status, size = self.head(urls[kind])
                if status != 200:
                    raise FetchError(f"{urls[kind]}: HEAD status {status}")
                # A mirror may answer HEAD without a usable Content-Length (the PIC copy returns 0),
                # so the size cross-check is skipped there; verify_sha below is the integrity gate
                # either way, and it deletes a bad file so that a rerun downloads it again.
                log(f"  fetch {names[kind]} ({size if size else 'size unknown'} B)")
                self.download(urls[kind], d / names[kind], expected_size=size or None)
            self.verify_sha(d, names)
            marker.write_text("ok\n")
        return dirs

    @staticmethod
    def verify_sha(d, names):
        """Python equivalent of `sha256sum -c`, restricted to the coadd and redrock files."""
        sums = parse_sha256sum((Path(d) / names["sha256sum"]).read_text())
        for kind in ("coadd", "redrock"):
            fn = names[kind]
            if fn not in sums:
                raise FetchError(f"{fn} not listed in {names['sha256sum']}")
            got = sha256_file(Path(d) / fn)
            if got != sums[fn]:
                # remove the bad file so that a rerun downloads it again (download() would
                # otherwise accept a file of the expected size and fail the same way forever)
                (Path(d) / fn).unlink()
                raise FetchError(f"SHA256 mismatch for {fn}: {got} != {sums[fn]} "
                                 f"(file removed; rerun to download it again)")


# ---------------------------------------------------------------- raw archive handling
def prog_names(pix, prog):
    """{kind: filename} (coadd, redrock, sha256sum) of one pixel and programme."""
    return {k: fn for p, k, fn, _ in config.pixel_urls(pix) if p == prog}


def dest_verified(d, pix, prog) -> bool:
    """True iff `d` holds the coadd, redrock and sha256sum files of (pix, prog), the SHA256_OK
    marker, and the coadd/redrock hashes match the DESI .sha256sum (re-hashed now)."""
    d = Path(d)
    names = prog_names(pix, prog)
    if not (d / config.SHA_MARKER).exists():
        return False
    if not all((d / fn).exists() for fn in names.values()):
        return False
    try:
        Fetcher.verify_sha(d, names)
    except (FetchError, OSError):
        return False
    return True


def drop_page_cache(path):
    """Ask the kernel to evict ``path``'s cached pages, so the next read comes from the mount
    (the raw archive may be CIFS with cache=strict: a read right after the copy could be served locally)."""
    path = Path(path)
    if not path.exists() or not hasattr(os, "posix_fadvise"):
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    finally:
        os.close(fd)


def move_verified(src, dest, pix, prog, log=print):
    """Crash-safe move of the staged raw files of (pix, prog) to the permanent directory `dest`.

    Every file is copied to ``<name>.part`` and renamed into place; the coadd and redrock hashes
    are then re-computed AT THE DESTINATION against the DESI .sha256sum; only after that is the
    SHA256_OK marker written there (last).  The staged copy is deleted only once the destination
    is verified.  A failed destination check removes the bad destination files and raises, and
    the staged copy is kept so that a rerun can repeat the move.  Idempotent.
    """
    src, dest = Path(src), Path(dest)
    names = prog_names(pix, prog)
    dest.mkdir(parents=True, exist_ok=True)
    if not dest_verified(dest, pix, prog):
        marker = dest / config.SHA_MARKER
        if marker.exists():
            marker.unlink()                 # never leave a marker next to unverified files
        for f in sorted(src.iterdir()):
            if f.name == config.SHA_MARKER or f.name.endswith(".part"):
                continue
            tmp = dest / (f.name + ".part")
            shutil.copyfile(f, tmp)
            os.replace(tmp, dest / f.name)
        for fn in names.values():
            drop_page_cache(dest / fn)          # re-hash reads the raw archive, not this client's cache
        try:
            Fetcher.verify_sha(dest, names)     # unlinks a file whose hash is wrong
        except (FetchError, OSError) as exc:
            for fn in names.values():
                (dest / fn).unlink(missing_ok=True)
            raise FetchError(f"{dest}: raw files failed re-hash at the destination: {exc}") from exc
        tmp = dest / (config.SHA_MARKER + ".part")
        tmp.write_text("ok\n")
        os.replace(tmp, marker)             # marker last: it exists only for a verified directory
        log(f"  moved + verified {dest}")
    if src.exists():
        (src / config.SHA_MARKER).unlink(missing_ok=True)   # a half-deleted staged dir is not trusted
        shutil.rmtree(src)
        try:
            src.parent.rmdir()              # tidy empty <pix//100> level; harmless if not empty
        except OSError:
            pass
    return sorted(str(dest / fn) for fn in names.values()) + [str(dest / config.SHA_MARKER)]
