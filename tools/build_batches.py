"""Write the fixed batch membership: batches/batch_000..083.txt, batch_ref22.txt, index.json.

Usage: python tools/build_batches.py --seed 20261028 [--out-dir DIR]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desi_galaxy_spectra.batches import BATCH_DIR, build  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--out-dir", type=Path, default=BATCH_DIR)
    args = ap.parse_args(argv)
    build(args.seed, args.out_dir)
    print(f"wrote batches (seed {args.seed}) to {args.out_dir}")


if __name__ == "__main__":
    main()
