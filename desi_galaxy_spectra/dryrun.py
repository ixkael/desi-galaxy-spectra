"""`extract --dry-run`: HTTP HEAD only, no file downloads.

Reports exact download volume per pixel, missing files, free space on staging and the raw archive, the
projected clean-galaxy counts (from pixel_lists/counts_class_chunk.csv, step 1), the projected
package size (gate-measured bytes per object, with and without resolution) and the projected time
(gate-measured seconds per pixel pair).  The two measured constants are read from the gate's
gate_measurements.json; nothing is hard-coded.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from . import config
from .fetch import Fetcher, free_bytes

REQUIRED_MEASUREMENTS = ("seconds_per_pixel_pair_total", "bytes_per_object_with_resolution",
                         "bytes_per_object_without_resolution")


class DryRunError(RuntimeError):
    pass


def load_measurements(path=config.GATE_MEASUREMENTS):
    path = Path(path)
    if not path.exists():
        raise DryRunError(f"gate measurements not found: {path} (run the gate first)")
    m = json.loads(path.read_text())
    missing = [k for k in REQUIRED_MEASUREMENTS if k not in m]
    if missing:
        raise DryRunError(f"{path} lacks {missing}")
    return m


def read_pixel_table(path=config.PIXEL_TABLE_CSV):
    with open(path) as f:
        return {int(r["healpix"]): r for r in csv.DictReader(f)}


def read_counts(path=config.COUNTS_CSV, sample="drawn100", version="extractor_cut",
                per_object_photsys_S="False"):
    """{(class, chunk): count} for one (sample, version, per_object_photsys_S) block."""
    out = {}
    order = []
    with open(path) as f:
        for r in csv.DictReader(f):
            if (r["sample"], r["version"], r["per_object_photsys_S"]) == \
                    (sample, version, str(per_object_photsys_S)):
                out[(r["class"], r["chunk"])] = int(r["count"])
                if r["chunk"] not in order:
                    order.append(r["chunk"])
    if not out:
        raise DryRunError(f"no rows for {sample}/{version}/{per_object_photsys_S} in {path}")
    return out, order


def head_pixel(fetcher, pix):
    """{(prog, kind): (status, size)} for one pixel.  HEAD requests only."""
    res = {}
    for prog, kind, fn, url in config.pixel_urls(pix):
        res[(prog, kind)] = fetcher.head(url)
    return res


def run(pixels, raw_root, shard_dir, staging, out_dir=config.DRY_RUN_DIR, fetcher=None,
        measurements_path=config.GATE_MEASUREMENTS, workers=1, sample="drawn100", log=print,
        table_path=config.PIXEL_TABLE_CSV, counts_path=config.COUNTS_CSV):
    fetcher = fetcher or Fetcher()
    meas = load_measurements(measurements_path)
    table = read_pixel_table(table_path)
    counts, chunk_order = read_counts(counts_path, sample=sample)
    drawn = {p for p, r in table.items() if r.get("drawn") == "1"}
    warn = []
    if sample == "drawn100" and set(pixels) != drawn:
        warn.append("pixel list differs from the drawn-100 set: the class x chunk counts below "
                    "refer to the drawn-100 sample, not to this list")
    bpo_r, bpo_n = (meas["bytes_per_object_with_resolution"],
                    meas["bytes_per_object_without_resolution"])
    spp = meas["seconds_per_pixel_pair_total"]

    rows, missing = [], []
    for pix in pixels:
        h = head_pixel(fetcher, pix)
        row = {"healpix": pix}
        tot, n_ok = 0, 0
        for (prog, kind), (status, size) in sorted(h.items()):
            ok = status == 200 and size is not None
            row[f"{prog}_{kind}_bytes"] = size if ok else ""
            row[f"{prog}_{kind}_status"] = status
            if ok:
                tot += size
                n_ok += 1
            else:
                missing.append(f"{pix} {prog} {kind}: HTTP {status}")
        tr = table.get(pix)
        ncg = int(tr["n_clean_galaxies"]) if tr else None
        row.update({
            "files_ok": n_ok, "files_expected": len(h), "download_bytes": tot,
            "n_clean_galaxies_step1": ncg if ncg is not None else "",
            "frac_photsys_S": tr["frac_S"] if tr else "",
            "proj_package_bytes_with_resolution": int(ncg * bpo_r) if ncg is not None else "",
            "proj_package_bytes_without_resolution": int(ncg * bpo_n) if ncg is not None else "",
            "proj_seconds_pixel_pair": spp,
        })
        rows.append(row)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cols = list(rows[0].keys()) if rows else []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with open(out_dir / "per_pixel.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    classes = sorted({c for c, _ in counts})
    with open(out_dir / "projected_counts_class_chunk.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["chunk"] + classes + ["total"])
        for ch in chunk_order:
            vals = [counts.get((c, ch), 0) for c in classes]
            w.writerow([ch] + vals + [sum(vals)])

    total_dl = sum(r["download_bytes"] for r in rows)
    max_dl = max((r["download_bytes"] for r in rows), default=0)
    n_pixel_table = sum(r["n_clean_galaxies_step1"] for r in rows
                        if r["n_clean_galaxies_step1"] != "")
    # package size: class x chunk counts of the step-1 sample when the list is that sample
    n_clean = sum(counts.values()) if not warn else n_pixel_table
    proj_r, proj_n = n_clean * bpo_r, n_clean * bpo_n
    stg_free, nas_free = free_bytes(staging), free_bytes(shard_dir)
    nas_raw_free = free_bytes(raw_root)
    n_pairs = len(pixels)
    summary = {
        "n_pixels": n_pairs, "n_missing_files": len(missing), "missing": missing,
        "download_bytes_total": total_dl, "download_bytes_max_pixel": max_dl,
        "staging_free_bytes": stg_free, "nas_shard_dir_free_bytes": nas_free,
        "nas_raw_root_free_bytes": nas_raw_free,
        "staging_ok": stg_free >= max_dl * max(1, workers) + config.STAGING_MARGIN_BYTES,
        "staging_needed_bytes": max_dl * max(1, workers) + config.STAGING_MARGIN_BYTES,
        "nas_raw_needed_bytes": total_dl,
        "n_clean_galaxies_projected_total": n_clean,
        "n_clean_galaxies_pixel_table_total": n_pixel_table,
        "projected_package_bytes_with_resolution": int(proj_r),
        "projected_package_bytes_without_resolution": int(proj_n),
        "projected_seconds_serial": spp * n_pairs,
        "projected_seconds_wall_assuming_linear_scaling": spp * n_pairs / max(1, workers),
        "workers_assumed": workers,
        "gate_constants": {"seconds_per_pixel_pair_total": spp,
                           "bytes_per_object_with_resolution": bpo_r,
                           "bytes_per_object_without_resolution": bpo_n,
                           "source": str(measurements_path)},
        "counts_sample": sample,
        "projected_counts_class_chunk": {f"{c}|{ch}": v for (c, ch), v in counts.items()},
        "not_projected": ["download time (no network rate measured)",
                          "shard size (class-tagged object counts per pixel are not in the "
                          "pixel lists)"],
        "warnings": warn,
    }
    (out_dir / "dry_run_summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    log(f"dry run: {n_pairs} pixels, {total_dl / 1e9:.2f} GB to download, "
        f"{len(missing)} missing files")
    for w_ in warn:
        log("WARNING: " + w_)
    log(f"staging free {stg_free / 1e9:.1f} GB (need {summary['staging_needed_bytes'] / 1e9:.1f}); "
        f"shard dir free {nas_free / 1e9:.1f} GB (raw files kept: {total_dl / 1e9:.1f} GB)")
    log(f"projected package {proj_r / 1e9:.2f} GB with resolution, {proj_n / 1e9:.2f} GB without")
    log(f"projected time {summary['projected_seconds_serial'] / 3600:.2f} h serial "
        f"({summary['projected_seconds_wall_assuming_linear_scaling'] / 3600:.2f} h with "
        f"{max(1, workers)} workers, if linear)")
    log(f"outputs in {out_dir}")
    return summary
