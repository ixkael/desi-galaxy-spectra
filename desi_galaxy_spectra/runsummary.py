"""The run record of one batch: RUN_SUMMARY.md and run_summary.json.

Structure follows the run-summary template shipped in ``templates/`` (and its JSON schema).
Every number is read from the files the run produced: ``extract_log.jsonl``, the manifest, the
verify report ``VERIFY.md``, the chunk files and ``status.json``.  A field that cannot be filled
reads ``not produced: <reason>``; nothing is estimated.
"""
from __future__ import annotations

import datetime
import json
import os
import platform
import re
import shlex
import socket
import subprocess
import sys
from pathlib import Path

from . import batches, config, manifest
from .batches import read_batch
from .settings import Settings
from .status import batch_dirs, chunk_counts

_CHECK_RE = re.compile(r"^\[(PASS|FAIL|SKIPPED)\] (.+)$")


def np_(reason: str) -> str:
    """The label of a value that could not be produced."""
    return f"not produced: {reason}"


def _git(args) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(config.REPO_ROOT), *args], capture_output=True,
                           text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _repo_state() -> tuple[str, bool | None]:
    """(short commit, dirty) of the installed checkout; the reason when it is not a checkout."""
    sha = _git(["rev-parse", "--short", "HEAD"])
    if sha is None:
        return np_("the package is not running from a git checkout"), None
    status = _git(["status", "--porcelain"])
    return sha, (None if status is None else bool(status))


def _read_log(shard_dir: Path, out: Path) -> list[dict]:
    for p in (shard_dir / "extract_log.jsonl", out / "extract_log.jsonl"):
        if p.is_file():
            return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]
    return []


def _log_totals(records: list[dict], pixels: list[int]) -> dict:
    """Per-pixel extraction numbers from the log (the last successful record of each pixel that
    reports an object count; pixels that were skipped on a rerun keep their earlier record)."""
    ok = {}
    for r in records:
        if "n_objects" in r and "error" not in r:
            ok[int(r["healpix"])] = r
    have = [ok[p] for p in pixels if p in ok]
    if not have:
        return {}
    return {"n_with_record": len(have),
            "n_objects": [r["n_objects"] for r in have],
            "seconds": [r["seconds_total"] for r in have],
            "shard_bytes": [r["shard_bytes"] for r in have],
            "rss": [r["peak_rss_mb"] for r in have],
            "n_error_lines": sum(1 for r in records if "error" in r)}


def _median(v):
    v = sorted(v)
    n = len(v)
    return v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2])


def _hms(sec: float) -> str:
    sec = float(sec)
    return f"{int(sec // 3600)}:{int(sec % 3600 // 60):02d}:{sec % 60:05.2f}"


def _fmt(n) -> str:
    return f"{n:,}" if isinstance(n, int) else str(n)


def write_run_summary(s: Settings, batch_id: str, timings: dict[str, float]) -> tuple[Path, Path]:
    """Write ``RUN_SUMMARY.md`` and ``run_summary.json`` into the batch output directory.

    ``timings`` maps a stage of the run (fetch, extract, package, manifest, md5, verify) to its
    wall time in seconds; a stage missing from it reads ``not produced``.
    """
    pixels = read_batch(batch_id)
    shard_dir, out = batch_dirs(s, batch_id)
    man = manifest.load_manifest(out / "manifest.json")
    vm = out / "VERIFY.md"
    if not vm.is_file():
        raise FileNotFoundError(f"{vm}: the run summary needs the verify report")
    checks = [(m.group(1), m.group(2)) for ln in vm.read_text().splitlines()
              if (m := _CHECK_RE.match(ln))]
    passed = [n for t, n in checks if t == "PASS"]
    failed = [n for t, n in checks if t == "FAIL"]
    verify_line = ("VERIFY PASSED" if "RESULT: PASS" in vm.read_text().splitlines()
                   else "VERIFY FAILED")
    counts = chunk_counts(out)
    st_path = out / "status.json"
    st = json.loads(st_path.read_text()) if st_path.is_file() else {}
    nbytes = st.get("bytes", {})
    log = _read_log(shard_dir, out)
    tot = _log_totals(log, pixels)
    sha, dirty = _repo_state()
    host = socket.gethostname()
    today = datetime.date.today().isoformat()
    run_id = f"{today}-dgs-batch-{batch_id}"
    command = shlex.join(sys.argv)

    seed_file = batches.BATCH_DIR / "index.json"
    seed = None
    if seed_file.is_file():
        try:
            seed = int(json.loads(seed_file.read_text())["seed"])
        except (ValueError, KeyError, TypeError):
            seed = None
    seeds_txt = str(seed) if seed is not None else np_("no batches/index.json beside the batch lists")

    if tot and tot["n_with_record"] == len(pixels):
        rows_before = int(sum(tot["n_objects"]))
    elif tot:
        rows_before = np_(f"extract_log.jsonl has records for {tot['n_with_record']} of "
                          f"{len(pixels)} pixels")
    else:
        rows_before = np_("extract_log.jsonl has no record of the shards of this batch")
    rows_after = counts["n_clean"]
    removed = (f"{100.0 * (1 - rows_after / rows_before):.2f}" if isinstance(rows_before, int)
               and rows_before else np_("rows before unknown"))

    def tm(key):
        return float(timings[key]) if key in timings else None

    def tm_txt(key):
        v = tm(key)
        return _hms(v) if v is not None else np_("stage not run in this record")

    wall = sum(float(v) for v in timings.values()) if timings else None
    raw_b = nbytes.get("raw")
    chunk_b = counts["bytes"]
    shard_b = nbytes.get("shards")

    # ------------------------------------------------------------------ markdown
    L = [f"# Run summary: {run_id}", "",
         "Written by the engineer role. Facts and diagnostics only; no interpretation. Fetch, "
         "extraction, per-pixel shards and redshift-chunk packaging of one fixed batch of "
         f"HEALPix pixels (batch {batch_id}, {len(pixels)} pixels). Status: "
         f"{'DONE' if not failed else 'FAILED'}.", "",
         "## Provenance", "| Field | Value |", "|---|---|",
         f"| Repo, commit | `desi-galaxy-spectra` @ `{sha}` (dirty: "
         f"{'not produced' if dirty is None else ('yes' if dirty else 'no')}), "
         f"version {config.KIT_VERSION} |",
         f"| Command | `{command}` |", "| Profile | engineer |",
         f"| Machine, environment | `{host}`, Python {platform.python_version()} on "
         f"{platform.system()} {platform.machine()}, lockfile sha256 `{config.lockfile_sha256()}` |",
         f"| Random seeds | {seeds_txt} |",
         f"| Inputs | {len(pixels)} pixels, bright and dark programmes, raw coadd and Redrock "
         f"files sha256-verified against the DESI checksums: "
         + (f"{_fmt(raw_b)} B" if isinstance(raw_b, int)
            else np_("raw files were not measured before removal")) + " |",
         f"| Wall time | {_hms(wall) if wall is not None else np_('no timings given')} "
         f"(fetch {tm_txt('fetch')}; extraction {tm_txt('extract')}; packaging "
         f"{tm_txt('package')}; manifest {tm_txt('manifest')}; md5 {tm_txt('md5')}; "
         f"verify {tm_txt('verify')}) |", "",
         "## Model and configuration",
         f"- Stage: per-pixel extraction, per-pixel shard, redshift-chunk packaging "
         f"(version {config.KIT_VERSION}), {s.workers} extraction worker(s), resolution "
         f"{'included' if s.with_resolution else 'not included'}. No fit, no free parameters.",
         "- Selection in the chunks: `is_galaxy & is_clean_galaxy`.", "",
         "| # | Cut expression | Rows before | Rows after | Removed (%) | Reference |",
         "|---|---|---|---|---|---|",
         f"| 1 | class-tagged objects in the shards to `is_galaxy & is_clean_galaxy` in the "
         f"chunk files | {_fmt(rows_before)} | {_fmt(rows_after)} | {removed} | "
         f"`extract_log.jsonl` (sum of `n_objects`); chunk files |", "",
         "## Best-fit parameters", "No fit was run: not produced: no fit.", "",
         "## Diagnostics",
         f"`dgs` verify (report `VERIFY.md`): {verify_line}.", "",
         "| Check | Result |", "|---|---|"]
    L += [f"| {n} | {t} |" for t, n in checks]
    L += ["", "Counts and timings (from `extract_log.jsonl`, the chunk files, `status.json`):", "",
          "| Quantity | Value |", "|---|---|"]
    if tot:
        L += [f"| Shards with a log record, failed lines | {tot['n_with_record']} of {len(pixels)}; "
              f"{tot['n_error_lines']} error line(s) in the log |",
              f"| Objects per shard | sum {_fmt(int(sum(tot['n_objects'])))}; min "
              f"{_fmt(int(min(tot['n_objects'])))}; median {_median(tot['n_objects']):,.0f}; max "
              f"{_fmt(int(max(tot['n_objects'])))} |",
              f"| Per-pixel time (`seconds_total`) | sum {sum(tot['seconds']):,.1f} s; min "
              f"{min(tot['seconds']):,.1f} s; median {_median(tot['seconds']):,.1f} s; max "
              f"{max(tot['seconds']):,.1f} s |",
              f"| Per-pixel peak RSS | max {max(tot['rss']):,.1f} MB |"]
    else:
        L += ["| Per-pixel extraction numbers | " + np_("extract_log.jsonl has no record of the "
                                                         "shards of this batch") + " |"]
    L += ["| Shard bytes | " +(_fmt(shard_b) if isinstance(shard_b, int)
                                else np_("shards were not measured")) + " |",
          f"| Clean objects in the chunk files | BGS {_fmt(counts['n_BGS'])}; ELG "
          f"{_fmt(counts['n_ELG'])}; LRG {_fmt(counts['n_LRG'])}; total {_fmt(rows_after)} |",
          f"| Chunk files | {counts['n_chunk_files']}, sum of sizes {_fmt(chunk_b)} B "
          f"(a chunk file is absent when no clean object falls in its redshift range) |",
          "| Physical assertions | z within each chunk's [z_lo, z_hi) and equal to the shard value "
          "(see the checks above); no fit, so no chi-square, residual, pull or convergence "
          "diagnostic: not produced: no fit |", "",
          "## Outputs", "| Path | What it is |", "|---|---|",
          f"| `{out}/` | {counts['n_chunk_files']} chunk files, `manifest.json`, `MD5SUMS.txt`, "
          "`VERIFY.md`, `README.md`, `status.json`, this summary |",
          f"| `{shard_dir}/` | shards `healpix_<pixel>.h5` and `extract_log.jsonl` "
          f"(shards: {st.get('shard_retention_action', 'not produced: no status.json')}) |",
          f"| `{s.raw_root}/` | raw coadd, Redrock and checksum files "
          f"(raw files: {st.get('raw_retention_action', 'not produced: no status.json')}) |", "",
          "## Checks and status",
          f"- Gates passed: {', '.join(passed) if passed else 'none'}; gates failed: "
          f"{', '.join(failed) if failed else 'none'}.",
          "- Change log: none.",
          "- Blockers / open items: none recorded by the driver.",
          "- Needs my decision: none from this run.", "",
          "## Not interpreted", "No physical conclusion is drawn here.", ""]
    md_path = out / "RUN_SUMMARY.md"
    _write(md_path, "\n".join(L))

    # ---------------------------------------------------------------------- json
    def sec(v):
        return v if v is not None else np_("stage not run in this record")

    secondary = {"verify": verify_line, "n_pixels": len(pixels),
                 "n_chunk_files": counts["n_chunk_files"], "chunk_bytes": counts["bytes"],
                 "n_BGS": counts["n_BGS"], "n_ELG": counts["n_ELG"], "n_LRG": counts["n_LRG"],
                 "n_clean_objects_packaged": rows_after,
                 "manifest_files": len(man["files"])}
    if tot:
        secondary.update({"n_shards_with_log_record": tot["n_with_record"],
                          "n_objects_shards": int(sum(tot["n_objects"])),
                          "per_pixel_seconds_total_sum": round(sum(tot["seconds"]), 1),
                          "per_pixel_seconds_max": round(max(tot["seconds"]), 1),
                          "per_pixel_peak_rss_mb_max": round(max(tot["rss"]), 1)})
    if isinstance(shard_b, int):
        secondary["shard_bytes"] = shard_b
    js = {
        "run_id": run_id, "profile": "engineer",
        "provenance": {
            "repo": "desi-galaxy-spectra", "git_sha": sha, "dirty": dirty, "command": command,
            "machine": host,
            "environment": f"version {config.KIT_VERSION}, lockfile sha256 "
                           f"{config.lockfile_sha256()}",
            "seeds": [seed] if seed is not None else [],
            "wall_time_s": {k: sec(tm(k)) for k in
                            ("fetch", "extract", "package", "manifest", "md5", "verify")},
            "inputs": [{"path": str(s.raw_root), "hash": "sha256 verified per file",
                        **({"bytes": raw_b} if isinstance(raw_b, int) else {})}]},
        # the schema wants an integer for rows_before: when the log cannot supply it the cut is
        # left out of the list (the markdown row says why) rather than given a made-up count
        "model": {"name": f"dgs {config.KIT_VERSION} extraction + shards + chunk packaging (no fit)",
                  "n_free_parameters": 0,
                  "selection": ([{"cut": "class-tagged objects to is_galaxy & is_clean_galaxy",
                                  "rows_before": rows_before, "rows_after": rows_after,
                                  "reference": "extract_log.jsonl"}]
                                if isinstance(rows_before, int) else [])},
        "parameters": [],
        "diagnostics": {
            "chi2_reduced": np_("no fit"), "residuals": [],
            "pulls": {"mean": np_("no fit"), "width": np_("no fit"),
                      "tail_fraction_3sigma": np_("no fit")},
            "convergence": {"converged": np_("no fit")},
            "physical_assertions": [{"assertion": n, "passed": t == "PASS"}
                                    for t, n in checks if t != "SKIPPED"],
            "secondary": secondary},
        "outputs": [
            {"path": f"{out}/", "kind": "other",
             "description": "chunk files, manifest.json, MD5SUMS.txt, VERIFY.md, README.md, "
                            "status.json, RUN_SUMMARY.md"},
            {"path": f"{shard_dir}/", "kind": "log",
             "description": "shards and extract_log.jsonl"}],
        "status": {"gates_passed": passed, "gates_failed": failed, "change_log": [],
                   "blockers": [], "needs_decision": []},
    }
    js_path = out / "run_summary.json"
    _write(js_path, json.dumps(js, indent=1) + "\n")
    return md_path, js_path


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
