"""Fixed membership of the 84 numbered processing batches and the 22-pixel reference batch.

Batch 000 is the drawn 100-pixel dry-run list.  The other 83 numbered batches split the
remaining eligible pixels, permuted with a seeded generator.  ``ref22`` is the 22 existing
reference pixels.  The membership is committed under ``batches/``; ``build`` regenerates it.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import numpy as np

from .config import PIXEL_LISTS, PIXEL_TABLE_CSV, REPO_ROOT, read_pixel_list

BATCH_DIR = REPO_ROOT / "batches"
N_NUMBERED = 84          # 000 (drawn) + 83 permuted splits
N_SPLITS = N_NUMBERED - 1
DRAWN_LIST = PIXEL_LISTS / "dr1_decals_random100.txt"


def batch_ids(include_ref22: bool = True) -> list[str]:
    ids = [f"{i:03d}" for i in range(N_NUMBERED)]
    if include_ref22:
        ids.append("ref22")
    return ids


def read_batch(batch_id: str) -> list[int]:
    return read_pixel_list(BATCH_DIR / f"batch_{batch_id}.txt")


def _read_table(path: Path = PIXEL_TABLE_CSV) -> list[dict]:
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def _header(seed: int, rule: str) -> str:
    return f"# seed {seed}; rule: {rule}; see index.json\n"


def _body(pixels) -> str:
    return "".join(f"{int(p)}\n" for p in sorted(pixels))


def build(seed: int, out_dir: Path = BATCH_DIR, table_csv: Path = PIXEL_TABLE_CSV,
          drawn_list: Path = DRAWN_LIST) -> None:
    """Write batch_000..083.txt, batch_ref22.txt and index.json into ``out_dir``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    table = _read_table(table_csv)
    clean = {int(r["healpix"]): int(r["n_clean_galaxies"]) for r in table}
    frac_s = {int(r["healpix"]): float(r["frac_S"]) for r in table}

    drawn = sorted(read_pixel_list(drawn_list))
    rest = sorted(int(r["healpix"]) for r in table if r["eligible"] == "1" and r["drawn"] == "0")
    ref22 = sorted(int(r["healpix"]) for r in table if r["existing_22"] == "1")

    perm = np.random.default_rng(seed).permutation(np.array(rest, dtype=np.int64))
    groups = np.array_split(perm, N_SPLITS)

    members = {"000": drawn}
    rules = {"000": "the drawn 100-pixel dry-run list pixel_lists/dr1_decals_random100.txt, sorted"}
    split_rule = (f"eligible and not drawn, sorted ascending, permuted by "
                  f"numpy.random.default_rng({seed}).permutation, split by "
                  f"numpy.array_split into {N_SPLITS} groups, each group sorted")
    for i, g in enumerate(groups, start=1):
        bid = f"{i:03d}"
        members[bid] = sorted(int(p) for p in g)
        rules[bid] = split_rule
    members["ref22"] = ref22
    rules["ref22"] = "the 22 existing reference pixels (existing_22 == 1), sorted"

    index = {
        "seed": seed,
        "numpy_version": np.__version__,
        "pixel_table_sha256": hashlib.sha256(Path(table_csv).read_bytes()).hexdigest(),
        "batches": {},
        "ref22_photsys_S_fraction": {str(p): frac_s[p] for p in ref22},
    }
    for bid, pix in members.items():
        (out_dir / f"batch_{bid}.txt").write_text(_header(seed, rules[bid]) + _body(pix))
        index["batches"][bid] = {"n_pixels": len(pix),
                                 "projected_clean": sum(clean[p] for p in pix)}
    (out_dir / "index.json").write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
