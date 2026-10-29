"""Take one fixed batch of HEALPix pixels through fetch, extract, package and verify.

``run_batch`` starts from the stage found on disk (see ``status.stage_on_disk``) and advances to
``until``.  Each stage calls the kit functions exactly as the stages of the original command-line
tool did: ``Fetcher.fetch_pixel``, ``run_pixel_job``, ``package``, ``write_manifest``, ``verify``.
A failure raises ``BatchError`` and leaves the batch at its stage; a rerun resumes from there.

Output directory ``<batch_root>/batch_<id>/``: chunk files, ``manifest.json``, ``MD5SUMS.txt``,
``VERIFY.md``, ``README.md``, ``RUN_SUMMARY.md``, ``run_summary.json``, ``status.json``.
Shard directory ``<shard_root>/batch_<id>/``: shards and ``extract_log.jsonl``.
"""
from __future__ import annotations

import datetime
import getpass
import json
import os
import shutil
import socket
import string
import time
import traceback
from pathlib import Path

from . import claims, config
from .batches import read_batch
from .fetch import Fetcher
from .manifest import write_manifest
from .package import md5_file, package
from .parallel import pmap
from .runsummary import write_run_summary
from .settings import Settings
from .shard import run_pixel_job
from .status import (DELIVERED, RAW_REMOVED, SHARDS_REMOVED, STAGES, batch_dirs, chunk_counts,
                     chunk_names, dir_bytes, raw_dirs, stage_on_disk)
from .verify import verify

__all__ = ["STAGES", "BatchError", "batch_dirs", "run_batch", "stage_on_disk"]

TEMPLATES = Path(__file__).parent / "templates"


class BatchError(RuntimeError):
    """A stage of a batch failed; the batch stays at the stage it had."""


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


# ------------------------------------------------------------------ extraction job runner
def _run_jobs(jobs, workers, logf, runner, log, mp_method="spawn"):
    """Run ``runner(job)`` for every job; append one JSON line per result, error or cancellation
    to ``logf``.  The first failure stops the run: pending jobs are cancelled (jobs already
    running finish and are logged).  Returns [(healpix, error text)] of the failed jobs."""
    failures = []

    def write(rec):
        with open(logf, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")

    def ok(res):
        write(res)
        log(f"pixel {res['healpix']}: " + ("skipped (shard exists)" if res.get("skipped")
            else f"{res['n_objects']} objects, {res['seconds_total']:.0f} s"))

    def bad(job, exc, tb):
        pix = int(job["pix"])
        write({"healpix": pix, "error": f"{type(exc).__name__}: {exc}", "traceback": tb})
        failures.append((pix, f"{type(exc).__name__}: {exc}"))
        log(f"pixel {pix}: FAILED {type(exc).__name__}: {exc}")

    if workers <= 1:
        for j in jobs:
            try:
                res = runner(j)
            except Exception as exc:        # noqa: BLE001 - recorded, then the run stops
                bad(j, exc, traceback.format_exc())
                break
            ok(res)
        return failures

    import multiprocessing as mp
    from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
    ex = ProcessPoolExecutor(workers, mp_context=mp.get_context(mp_method))
    try:
        futs = {ex.submit(runner, j): j for j in jobs}
        pending, stopping = set(futs), False
        while pending:
            # as_completed() is not used: it never wakes for futures cancelled by shutdown()
            done, _ = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
            for fut in done | {f for f in pending if f.cancelled()}:
                pending.discard(fut)
                j = futs[fut]
                if fut.cancelled():
                    write({"healpix": int(j["pix"]), "cancelled": True})
                    continue
                try:
                    res = fut.result()
                except Exception as exc:        # noqa: BLE001
                    bad(j, exc, "".join(traceback.format_exception(
                        type(exc), exc, exc.__traceback__)))
                    if not stopping:
                        stopping = True
                        ex.shutdown(wait=False, cancel_futures=True)
                    continue
                ok(res)
    finally:
        ex.shutdown(wait=True, cancel_futures=True)
    return failures


# ------------------------------------------------------------------------- stage steps
def _step_downloaded(s, bid, pixels, shard_dir, out, fetch, log):
    todo = [p for p in pixels
            if not all((d / config.SHA_MARKER).is_file()
                       for d in raw_dirs(s, [p]))]
    if not fetch:
        raise BatchError(f"batch {bid}: {len(todo)} of {len(pixels)} pixels are not downloaded "
                         f"(no {config.SHA_MARKER} marker); pass fetch=True to download them")
    f = Fetcher()
    for pix in todo:
        log(f"pixel {pix}")
        f.fetch_pixel(pix, s.raw_root, log=log)
        absent = [prog for prog in config.PROGRAMS
                  if not (config.raw_dir(s.raw_root, pix, prog) / config.SHA_MARKER).is_file()]
        if absent:
            raise BatchError(f"batch {bid}: fetch finished for pixel {pix} but the "
                             f"{config.SHA_MARKER} marker is missing for programme(s) "
                             f"{', '.join(absent)}")
    return {}


def _step_extracted(s, bid, pixels, shard_dir, out, fetch, log):
    shard_dir.mkdir(parents=True, exist_ok=True)
    todo = [p for p in pixels if not (shard_dir / config.shard_name(p)).is_file()]
    jobs = [dict(pix=p, shard_dir=str(shard_dir), staging=str(s.staging), keep_temp=False,
                 local_flat=None, redrock_dir=None, raw_root=str(s.raw_root), fetch=False)
            for p in todo]
    failures = _run_jobs(jobs, s.workers, shard_dir / "extract_log.jsonl", run_pixel_job, log)
    if failures:
        raise BatchError(f"batch {bid}: extract failed for {len(failures)} pixel(s): "
                         + ", ".join(str(p) for p, _ in failures)
                         + f" (first error: {failures[0][1]}; see {shard_dir / 'extract_log.jsonl'})")
    missing = [p for p in pixels if not (shard_dir / config.shard_name(p)).is_file()]
    if missing:
        raise BatchError(f"batch {bid}: extract finished but shards are missing for {missing}")
    return {}


def _step_packaged(s, bid, pixels, shard_dir, out, fetch, log):
    out.mkdir(parents=True, exist_ok=True)
    for name in ("manifest.json", "MD5SUMS.txt", "VERIFY.md", DELIVERED):   # stale derived files
        (out / name).unlink(missing_ok=True)
    for p in list(out.glob("*.partial")) + list(out.glob("*.tmp")):
        p.unlink()
    t = time.time()
    package(shard_dir, out, with_resolution=s.with_resolution, log=log, workers=s.workers)
    t_pkg = time.time() - t
    t = time.time()
    write_manifest(out, out / "manifest.json", log=log, workers=s.workers)
    return {"package": t_pkg, "manifest": time.time() - t}


def _step_verified(s, bid, pixels, shard_dir, out, fetch, log):
    for name in ("MD5SUMS.txt", "VERIFY.md", DELIVERED):
        (out / name).unlink(missing_ok=True)
    names = sorted(chunk_names(out) | {"manifest.json"})
    t = time.time()
    sums = list(zip(pmap(md5_file, [out / n for n in names], s.workers), names))
    _atomic_write(out / "MD5SUMS.txt", "".join(f"{m}  {n}\n" for m, n in sums))
    t_md5 = time.time() - t
    t = time.time()
    rep = verify(out, out / "manifest.json", shard_dir=shard_dir, log=log, workers=s.workers)
    t_ver = time.time() - t
    sizes = "".join(f"FILE {n} {(out / n).stat().st_size}\n" for n in names)
    text = (f"# Verification: batch {bid}\n\n"
            f"RESULT: {'PASS' if rep.ok else 'FAIL'}\n\n"
            f"- Date: {_now()}\n- Kit version: {config.KIT_VERSION}\n"
            f"- Lockfile sha256: {config.lockfile_sha256()}\n"
            f"- Pixels: {len(pixels)}\n- Run by: {getpass.getuser()}\n\n"
            f"## Report\n\n```\n{rep.text()}\n```\n\n## Files\n\n{sizes}")
    _atomic_write(out / "VERIFY.md", text)
    if not rep.ok:
        raise BatchError(f"batch {bid}: verify failed; see {out / 'VERIFY.md'}")
    cc = chunk_counts(out)
    tmpl = string.Template((TEMPLATES / "BATCH_README.md").read_text())
    _atomic_write(out / "README.md", tmpl.safe_substitute(
        batch=bid, n_pixels=len(pixels), kit_version=config.KIT_VERSION,
        lockfile_sha256=config.lockfile_sha256(), written=_now(), run_by=getpass.getuser(),
        n_chunk_files=cc["n_chunk_files"], n_clean=cc["n_clean"], n_BGS=cc["n_BGS"],
        n_ELG=cc["n_ELG"], n_LRG=cc["n_LRG"]))
    log_src = shard_dir / "extract_log.jsonl"
    if log_src.is_file():
        shutil.copyfile(log_src, out / "extract_log.jsonl")
    return {"md5": t_md5, "verify": t_ver}


def _step_delivered(s, bid, pixels, shard_dir, out, fetch, log):
    _atomic_write(out / DELIVERED, json.dumps({"batch": bid, "user": getpass.getuser(),
                                               "time": _now()}) + "\n")
    return {}


_STEPS = {"downloaded": ("fetch", _step_downloaded), "extracted": ("extract", _step_extracted),
          "packaged": ("package", _step_packaged), "verified": ("verify", _step_verified),
          "delivered": ("deliver", _step_delivered)}


# --------------------------------------------------------------------- status and retention
def _load_status(out: Path) -> dict:
    """The previous status.json (empty if there is none).  A file that cannot be parsed is a hard
    error: it is never replaced silently, since its timings and retention actions would be lost."""
    p = out / "status.json"
    if not p.is_file():
        return {}
    try:
        st = json.loads(p.read_text())
    except ValueError as exc:
        raise BatchError(f"{p} is not valid JSON ({exc}); inspect it, then delete it to let the "
                         "driver write a fresh one") from exc
    if not isinstance(st, dict):
        raise BatchError(f"{p} does not hold a JSON object; inspect it, then delete it")
    return st


def _write_status(s, bid, pixels, shard_dir, out, stage, prev, seconds, stamps,
                  raw_action, shard_action):
    """status.json: a record of the run.  The stage is never read back from it."""
    cc = chunk_counts(out) if stage in STAGES[3:] else {}
    prev_b = prev.get("bytes", {})
    if (out / RAW_REMOVED).is_file():
        raw_b = prev_b.get("raw", "not produced: raw files were removed before they were measured")
    else:
        raw_b = dir_bytes(raw_dirs(s, pixels))
    if (out / SHARDS_REMOVED).is_file():
        shard_b = prev_b.get("shards",
                             "not produced: shards were removed before they were measured")
    else:
        shard_b = sum((shard_dir / config.shard_name(p)).stat().st_size for p in pixels
                      if (shard_dir / config.shard_name(p)).is_file())
    st = {
        "batch": bid, "stage": stage, "timestamps": stamps, "seconds": seconds,
        "kit_version": config.KIT_VERSION, "lockfile_sha256": config.lockfile_sha256(),
        "counts": {"n_pixels": len(pixels), "n_chunk_files": cc.get("n_chunk_files", 0),
                   "n_BGS": cc.get("n_BGS", 0), "n_ELG": cc.get("n_ELG", 0),
                   "n_LRG": cc.get("n_LRG", 0), "n_clean": cc.get("n_clean", 0)},
        "bytes": {"raw": raw_b, "shards": shard_b, "chunks": cc.get("bytes", 0)},
        "raw_retention_action": raw_action, "shard_retention_action": shard_action,
        "run_by": getpass.getuser(), "host": socket.gethostname(),
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"), "updated": _now(),
    }
    out.mkdir(parents=True, exist_ok=True)
    _atomic_write(out / "status.json", json.dumps(st, indent=1) + "\n")
    return st


def _retain(s, pixels, shard_dir, out, log):
    """After verification: delete the raw files and the shards of this batch when the settings
    say so.  The marker goes first, so an interrupted deletion is still read as finished."""
    if not s.keep_raw_after_verify:
        (out / RAW_REMOVED).write_text("raw files of this batch were deleted after verification\n")
        for d in raw_dirs(s, pixels):
            if d.exists():
                shutil.rmtree(d)
            try:
                d.parent.rmdir()            # empty <pix//100> level; harmless if others remain
            except OSError:
                pass
        log("raw files deleted")
    if not s.keep_shards_after_verify:
        (out / SHARDS_REMOVED).write_text("shards of this batch were deleted after verification\n")
        for p in pixels:
            (shard_dir / config.shard_name(p)).unlink(missing_ok=True)
        log("shards deleted")


def run_batch(s: Settings, batch_id: str, fetch: bool = False, until: str = "verified",
              log=print) -> str:
    """Advance a batch from the stage on disk to ``until`` and return the stage reached.

    ``fetch=True`` downloads the raw files of pixels that lack them; without it a batch whose
    raw files are missing stops at ``queued`` with an error.  Raw files and shards of the batch
    are deleted after verification only when ``keep_raw_after_verify`` / ``keep_shards_after_verify``
    are false.
    """
    if until not in STAGES:
        raise ValueError(f"until must be one of {STAGES}, got {until!r}")
    claims.require_own_claim(s, batch_id)
    pixels = read_batch(batch_id)
    shard_dir, out = batch_dirs(s, batch_id)
    out.mkdir(parents=True, exist_ok=True)
    for pix in pixels:      # a killed run leaves <shard>.partial behind: never trusted, removed
        (shard_dir / (config.shard_name(pix) + ".partial")).unlink(missing_ok=True)
    stage = stage_on_disk(s, batch_id)
    prev = _load_status(out)
    seconds = dict(prev.get("seconds", {}))
    stamps = dict(prev.get("timestamps", {}))
    raw_action = prev.get("raw_retention_action", "pending")
    shard_action = prev.get("shard_retention_action", "pending")
    target = STAGES.index(until)
    log(f"batch {batch_id}: {len(pixels)} pixels; on disk: {stage}; target: {until}")

    advanced_to_verified = False
    while STAGES.index(stage) < target:
        nxt = STAGES[STAGES.index(stage) + 1]
        label, step = _STEPS[nxt]
        t0 = time.time()
        try:
            took = step(s, batch_id, pixels, shard_dir, out, fetch, log)
        except BatchError:
            raise
        except Exception as exc:        # noqa: BLE001 - re-raised with the stage and the batch
            raise BatchError(f"batch {batch_id}: {label} failed: "
                             f"{type(exc).__name__}: {exc}") from exc
        elapsed = time.time() - t0
        if nxt == "downloaded":
            took = {"fetch": elapsed}
        elif nxt == "extracted":
            took = {"extract": elapsed}
        seconds.update({k: round(v, 2) for k, v in took.items()})
        stage = nxt
        stamps[stage] = _now()
        advanced_to_verified = advanced_to_verified or stage == "verified"
        _write_status(s, batch_id, pixels, shard_dir, out, stage, prev, seconds, stamps,
                      raw_action, shard_action)
        log(f"batch {batch_id}: {stage}")

    if STAGES.index(stage) >= STAGES.index("verified") and target >= STAGES.index("verified"):
        if not s.keep_raw_after_verify or not s.keep_shards_after_verify:
            _retain(s, pixels, shard_dir, out, log)
        raw_action = "kept" if s.keep_raw_after_verify else "deleted"
        shard_action = "kept" if s.keep_shards_after_verify else "deleted"
        _write_status(s, batch_id, pixels, shard_dir, out, stage, _load_status(out),
                      seconds, stamps, raw_action, shard_action)
        if advanced_to_verified or not (out / "RUN_SUMMARY.md").is_file():
            try:
                write_run_summary(s, batch_id, seconds)
            except Exception as exc:    # noqa: BLE001
                raise BatchError(f"batch {batch_id}: run summary failed: "
                                 f"{type(exc).__name__}: {exc}") from exc
    elif stage != prev.get("stage") or not (out / "status.json").is_file():
        _write_status(s, batch_id, pixels, shard_dir, out, stage, prev, seconds, stamps,
                      raw_action, shard_action)
    return stage
