"""dgs: fetch, extract, package, manifest and verify DESI DR1 clean-galaxy spectra.

Directories not given on the command line (--raw-root, --shard-dir, --staging, --workers) come
from the settings file ($DGS_SETTINGS, ./settings.toml, or the top-level --settings option).

Single-step commands (as in the original kit):
  dgs fetch    --pixels FILE [--raw-root DIR]
  dgs extract  (--pixels FILE | --pixel N) [--raw-root DIR] [--shard-dir DIR] [--workers K]
               [--fetch] [--local-flat DIR --redrock-dir DIR] [--staging DIR]
               [--dry-run [--dry-run-out DIR]] [--keep-temp (gate only)]
  dgs package  [--shard-dir DIR] --out DIR [--no-resolution] [--only i,j] [--dry-run]
  dgs manifest --target DIR --out manifest.json
  dgs verify   --target DIR --manifest manifest.json (--shard-dir DIR | --no-shards)

Shared running over the fixed batches:
  dgs run      --batch ID [--claim] [--fetch] [--until STAGE]
  dgs claim    --batch ID          dgs release [--force] --batch ID          dgs next
  dgs status   [--deep]            dgs doctor                                dgs index
  dgs deliver  --batch ID
"""
import argparse
import os
import sys
import time
from pathlib import Path

from . import config

THREAD_VARS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMBA_NUM_THREADS")


def _log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ------------------------------------------------------------------------------- settings
def _settings(args, why):
    """The settings, loaded once per invocation; ``why`` names the option that needs them."""
    s = getattr(args, "_settings", None)
    if s is None:
        from .settings import load_settings
        try:
            s = load_settings(Path(args.settings) if args.settings else None)
        except (FileNotFoundError, ValueError) as exc:
            sys.exit(f"{why}: not given on the command line and the settings are unusable: {exc}")
        args._settings = s
    return s


def _pick(args, value, flag, attr):
    """``value`` if given, else the settings attribute ``attr`` (an error naming ``flag`` if the
    settings cannot be read)."""
    return value if value is not None else getattr(_settings(args, flag), attr)


def _pixels(args):
    if getattr(args, "pixel", None) is not None:
        return [int(args.pixel)]
    return config.read_pixel_list(args.pixels)


def _batch_id(text):
    from . import batches
    config.require_checkout()       # a clear message instead of "unknown batch" outside a checkout
    if not (batches.BATCH_DIR / f"batch_{text}.txt").is_file():
        raise argparse.ArgumentTypeError(f"unknown batch {text!r} (000-083 or ref22)")
    return text


# -------------------------------------------------------------------- single-step commands
def cmd_fetch(args):
    from .fetch import Fetcher
    raw_root = _pick(args, args.raw_root, "--raw-root", "raw_root")
    f = Fetcher()
    for pix in _pixels(args):
        _log(f"pixel {pix}")
        f.fetch_pixel(pix, raw_root, log=_log)
    return 0


def run_jobs(jobs, workers, logf, runner, mp_method="spawn"):
    """Run ``runner(job)`` for every job (see ``driver._run_jobs``); logs through ``_log``.
    Returns [(healpix, error text)] of the failed jobs."""
    from .driver import _run_jobs
    return _run_jobs(jobs, workers, logf, runner, _log, mp_method=mp_method)


def cmd_extract(args):
    pixels = _pixels(args)
    if args.dry_run:
        from . import dryrun
        if args.local_flat:
            sys.exit("--dry-run is a network (HEAD) check; not combinable with --local-flat")
        raw_root = _pick(args, args.raw_root, "--raw-root", "raw_root")
        shard_dir = _pick(args, args.shard_dir, "--shard-dir", "shard_root")
        staging = _pick(args, args.staging, "--staging", "staging")
        workers = _pick(args, args.workers, "--workers", "workers")
        out_dir = args.dry_run_out
        if out_dir is None:     # never the committed provenance directory
            out_dir = Path(_settings(args, "--dry-run-out").batch_root).parent / "dry-run"
        dryrun.run(pixels, raw_root or shard_dir, shard_dir, Path(staging), out_dir=out_dir,
                   workers=workers, log=_log)
        return 0
    if args.local_flat and not args.redrock_dir:
        sys.exit("--local-flat requires --redrock-dir")
    if args.local_flat and args.fetch:
        sys.exit("--local-flat (gate mode) cannot be combined with --fetch")
    if args.keep_temp and not args.local_flat:
        sys.exit("--keep-temp is a gate-only option (use with --local-flat)")
    if args.raw_root == "":
        sys.exit("--raw-root is required (the permanent raw archive; '' would mean the current "
                 "directory) unless --local-flat or --dry-run is given")
    raw_root = args.raw_root
    if raw_root is None:
        raw_root = "" if args.local_flat else _pick(args, None, "--raw-root", "raw_root")
    shard_dir = Path(_pick(args, args.shard_dir, "--shard-dir", "shard_root"))
    staging = Path(_pick(args, args.staging, "--staging", "staging"))
    workers = _pick(args, args.workers, "--workers", "workers")
    from .shard import run_pixel_job
    shard_dir.mkdir(parents=True, exist_ok=True)
    jobs = [dict(pix=p, shard_dir=str(shard_dir), staging=str(staging),
                 keep_temp=args.keep_temp, local_flat=args.local_flat,
                 redrock_dir=args.redrock_dir, raw_root=str(raw_root),
                 fetch=args.fetch) for p in pixels]
    failures = run_jobs(jobs, workers, shard_dir / "extract_log.jsonl", run_pixel_job)
    if failures:
        print(f"EXTRACT FAILED: {len(failures)} pixel(s): "
              + ", ".join(str(p) for p, _ in failures), file=sys.stderr, flush=True)
        return 1
    return 0


def cmd_package(args):
    from .package import package
    shard_dir = _pick(args, args.shard_dir, "--shard-dir", "shard_root")
    only = [int(x) for x in args.only.split(",")] if args.only else None
    package(shard_dir, args.out, with_resolution=not args.no_resolution, only=only,
            dry=args.dry_run, log=_log)
    return 0


def cmd_manifest(args):
    from .manifest import write_manifest
    write_manifest(args.target, args.out, log=_log)
    return 0


def cmd_verify(args):
    from .verify import verify
    if args.shard_dir and args.no_shards:
        sys.exit("--shard-dir and --no-shards are contradictory")
    rep = verify(args.target, args.manifest, shard_dir=args.shard_dir, no_shards=args.no_shards,
                 log=print)
    return 0 if rep.ok else 1


# ----------------------------------------------------------------- shared-running commands
def cmd_run(args):
    from . import claims
    from .driver import run_batch
    s = _settings(args, "settings")
    if args.claim:
        claims.require_own_claim(s, args.batch)
        if claims.read_claim(s, args.batch) is None:
            c = claims.claim(s, args.batch)
            _log(f"batch {args.batch}: claimed by {c['user']}")
    stage = run_batch(s, args.batch, fetch=args.fetch, until=args.until, log=_log)
    print(f"batch {args.batch}: {stage}")
    return 0


def cmd_claim(args):
    from . import claims
    c = claims.claim(_settings(args, "settings"), args.batch)
    print(f"batch {args.batch}: claimed by {c['user']} on {c['host']} at {c['time']}")
    return 0


def cmd_release(args):
    from . import claims
    claims.release(_settings(args, "settings"), args.batch, force=args.force)
    return 0


def cmd_next(args):
    from . import claims
    print(claims.next_unclaimed(_settings(args, "settings")))
    return 0


def cmd_status(args):
    from .status import write_status
    tsv, md = write_status(_settings(args, "settings"), deep=args.deep)
    print(md.read_text(), end="")
    print(f"written: {tsv}\nwritten: {md}")
    return 0


def cmd_doctor(args):
    from . import claims
    problems = claims.doctor(_settings(args, "settings"))
    for p in problems:
        print(p)
    print(f"doctor: {len(problems)} path(s) with a permission problem")
    return 1 if problems else 0


def cmd_deliver(args):
    from . import claims
    from .driver import BatchError, run_batch
    from .status import stage_on_disk
    s = _settings(args, "settings")
    claims.require_own_claim(s, args.batch)
    stage = stage_on_disk(s, args.batch, deep=True)
    if stage != "verified":
        raise BatchError(f"batch {args.batch} is at stage {stage!r}; only a verified batch "
                         f"(chunk files re-hashed) can be delivered")
    run_batch(s, args.batch, until="delivered", log=_log)
    print(f"batch {args.batch}: delivered")
    return 0


def cmd_index(args):
    from .index import build_index, check_unique
    s = _settings(args, "settings")
    print(f"written: {build_index(s)}")
    dups = check_unique(s)
    for m in dups:
        print(m)
    print(f"index: {len(dups)} duplicate (targetid, program) pair(s)")
    return 1 if dups else 0


# ---------------------------------------------------------------------------------- parser
def build_parser():
    from .driver import STAGES
    ap = argparse.ArgumentParser(prog="dgs", description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--settings", help="settings TOML (default: $DGS_SETTINGS, then ./settings.toml)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("fetch", help="download + sha256-verify the raw files of the listed pixels")
    p.add_argument("--pixels", required=True)
    p.add_argument("--raw-root", help="default: raw_root of the settings")
    p.set_defaults(fn=cmd_fetch)

    p = sub.add_parser("extract", help="extract pixels into shards")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--pixels")
    g.add_argument("--pixel", type=int)
    p.add_argument("--raw-root", default=None,
                   help="permanent raw archive (default: raw_root of the settings; not needed "
                        "with --local-flat)")
    p.add_argument("--shard-dir", help="default: shard_root of the settings")
    p.add_argument("--workers", type=int, default=None,
                   help="pixel pairs processed in parallel (default: workers of the settings)")
    p.add_argument("--fetch", action="store_true", help="stream the download into staging first")
    p.add_argument("--local-flat", help="gate mode: flat dir of coadd-main-<prog>-<pix>.fits")
    p.add_argument("--redrock-dir", help="gate mode: dir of redrock-main-<prog>-<pix>.fits")
    p.add_argument("--staging", help="scratch (default: staging of the settings)")
    p.add_argument("--dry-run", action="store_true",
                   help="HTTP HEAD only: sizes, missing files, free space, projections")
    p.add_argument("--dry-run-out",
                   help="where --dry-run writes its report (default: <batch_root>/../dry-run)")
    p.add_argument("--keep-temp", action="store_true",
                   help="GATE ONLY: keep the per-pixel v4-layout temp file in --shard-dir")
    p.set_defaults(fn=cmd_extract)

    p = sub.add_parser("package", help="cut clean-galaxy redshift chunks from the shards")
    p.add_argument("--shard-dir", help="default: shard_root of the settings")
    p.add_argument("--out", required=True)
    p.add_argument("--no-resolution", action="store_true")
    p.add_argument("--only", help="comma-separated chunk indices")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_package)

    p = sub.add_parser("manifest", help="write the content manifest of a directory")
    p.add_argument("--target", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_manifest)

    p = sub.add_parser("verify", help="check a directory against its manifest")
    p.add_argument("--target", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--shard-dir", help="enables the checks that need the shards "
                                       "(required unless --no-shards)")
    p.add_argument("--no-shards", action="store_true",
                   help="explicit opt-out: skip the shard-dependent completeness checks "
                        "(printed in the report)")
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("run", help="take one batch from the stage on disk to --until")
    p.add_argument("--batch", required=True, type=_batch_id)
    p.add_argument("--claim", action="store_true", help="claim the batch first (unless it is "
                                                         "already yours)")
    p.add_argument("--fetch", action="store_true", help="download the raw files that are missing")
    p.add_argument("--until", default="verified", choices=STAGES[1:])
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("claim", help="claim a batch (exclusive; fails if already claimed)")
    p.add_argument("--batch", required=True, type=_batch_id)
    p.set_defaults(fn=cmd_claim)

    p = sub.add_parser("release", help="release your claim on a batch")
    p.add_argument("--batch", required=True, type=_batch_id)
    p.add_argument("--force", action="store_true",
                   help="release another user's claim (recorded in claims.log)")
    p.set_defaults(fn=cmd_release)

    p = sub.add_parser("next", help="print the lowest batch (000-083) that is neither claimed "
                                    "nor delivered")
    p.set_defaults(fn=cmd_next)

    p = sub.add_parser("status", help="write and print status.tsv / status.md over all batches")
    p.add_argument("--deep", action="store_true", help="re-hash the chunk files (slow)")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("doctor", help="list paths that are not group-writable, in the wrong "
                                      "group, or directories without setgid (no changes)")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("deliver", help="mark a verified batch as delivered")
    p.add_argument("--batch", required=True, type=_batch_id)
    p.set_defaults(fn=cmd_deliver)

    p = sub.add_parser("index", help="write index.csv and check (targetid, program) uniqueness")
    p.set_defaults(fn=cmd_index)
    return ap


def main(argv=None):
    for k in THREAD_VARS:           # before any numpy-heavy import
        os.environ[k] = "1"
    os.umask(0o002)
    from .claims import ClaimError
    from .driver import BatchError
    try:
        args = build_parser().parse_args(argv)
        return args.fn(args)
    except (ClaimError, BatchError, config.CheckoutError) as exc:
        print(f"dgs: error: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
