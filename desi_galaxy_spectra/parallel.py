"""Map a function over items in worker processes (spawn), or serially for workers <= 1.

Results come back in the order of the items, so a parallel run writes the same products and
the same log order as the serial one.
"""
from __future__ import annotations

import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor


def pmap(fn, items, workers=1):
    items = list(items)
    n = min(int(workers or 1), len(items))
    if n <= 1:
        return [fn(x) for x in items]
    with ProcessPoolExecutor(max_workers=n, mp_context=mp.get_context("spawn")) as ex:
        return list(ex.map(fn, items))
