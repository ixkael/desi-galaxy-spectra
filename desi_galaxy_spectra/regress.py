"""Regression manifest and comparator with a 1-ULP tolerance for float datasets.

A *regression manifest* describes a set of HDF5 files (shards and chunk files) with one md5 per
dataset and one md5 per attribute:

    {"files": {rel: {"datasets": {h5path: {"dtype", "shape", "md5", "kind"}},
                     "attrs": {"<object path>@<name>": md5}}},
     "excluded": {"attrs": {...}, "datasets": {...}, "path_attrs": {...}, "path_datasets": {...}}}

Dataset md5 is ``manifest.dataset_md5`` (raw C-order bytes); attribute md5 is the md5 of
``manifest._canon_attr``.  ``kind`` is ``"float"`` (float16/32/64), ``"exact"`` (everything
else) or ``"provenance"`` (a dataset of ``PROVENANCE_DATASETS``: its value is never compared).
Path-valued items (``PATH_DATASETS``, ``PATH_ATTRS``) store the md5 of their basenames only.
``excluded`` holds the exclusion rules the manifest was built with, each with its reason.

``compare`` checks a target directory against a reference manifest under the criterion of the
build plan (PI 2026-10-27): file and dataset sets, dtypes, shapes, integer / boolean / string
datasets and all non-provenance attributes exact; a float dataset whose md5 differs is compared
element by element with the reference arrays and passes if every element is within 1 ULP of the
stored dtype; NaN must match NaN bit for bit; anything else fails.
"""
from __future__ import annotations

import dataclasses
import datetime
import fnmatch
import hashlib
import json
import platform
import posixpath
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import h5py
import numpy as np

from . import config
from .driver import batch_dirs, run_batch
from .manifest import _canon_attr, _row_blocks, dataset_md5, list_h5

# ------------------------------------------------------------------------- exclusion rules
PROVENANCE_ATTRS: dict[tuple[str, str], str] = {
    ("/", "created"): "timestamp of the packaging run",
    ("/", "script"): "name of the writer (the kit and dgs name themselves differently)",
    ("/", "kit_version"): "version of the code that wrote the shard",
    ("/", "lockfile_sha256"): "fingerprint of the environment lock of the code that wrote the "
                              "shard",
    ("/", "numpy_version"): "numpy version of the environment that wrote the shard",
    ("/", "python_version"): "Python version of the environment that wrote the shard",
    ("/", "desispec_version"): "desispec version of the environment that wrote the shard",
    ("/", "source_manifest_md5"): "md5 of the shard file bytes, which carry the timestamps and "
                                  "versions above",
    ("/source_metadata", "creation_date"): "timestamp of the extraction that produced the "
                                           "source shard (chunk-file copy)",
    ("/metadata", "creation_date"): "timestamp of the extraction, in the shard itself (the "
                                    "shard-side original of /source_metadata@creation_date)",
}

PROVENANCE_DATASETS: dict[str, str] = {
    "/source_shard_table/source_metadata/creation_date":
        "per-shard copy of /metadata@creation_date (extraction timestamp)",
}

PATH_DATASETS: tuple[str, ...] = ("/*/catalog/coadd_file",)
PATH_ATTRS: tuple[tuple[str, str], ...] = (("/", "input_files"),)
PATH_REASON = ("path of a raw file: the directory depends on the machine, so only the basename "
               "is compared")

ULP_TOLERANCE = 1

REFERENCE_STEMS = {"21852": "pixel21852", "batch000": "batch000", "ref22": "ref22"}
REFERENCE_TASKS = {"pixel21852": "Task 12", "batch000": "Task 13", "ref22": "Task 14"}
BATCH_OF_REFERENCE = {"batch000": "000", "ref22": "ref22"}
REGRESS_DIR = config.REPO_ROOT / "reference" / "regress"
PIXEL_21852 = 21852


class RegressError(RuntimeError):
    """The regression could not be run (missing reference, stage failure, unusable scratch)."""


def _akey(path: str, name: str) -> str:
    return f"{path}@{name}"


def exclusion_rules() -> dict:
    """The exclusion rules of this code, in the JSON form stored under ``excluded``."""
    return {
        "attrs": {_akey(p, k): r for (p, k), r in sorted(PROVENANCE_ATTRS.items())},
        "datasets": dict(sorted(PROVENANCE_DATASETS.items())),
        "path_attrs": {_akey(p, k): PATH_REASON for p, k in sorted(PATH_ATTRS)},
        "path_datasets": {p: PATH_REASON for p in sorted(PATH_DATASETS)},
    }


def _segments(path: str) -> list[str]:
    return [] if path.strip("/") == "" else path.strip("/").split("/")


def _match(pattern: str, path: str) -> bool:
    """Glob match segment by segment (``*`` never crosses a ``/``)."""
    ps, xs = _segments(pattern), _segments(path)
    return len(ps) == len(xs) and all(fnmatch.fnmatchcase(x, p) for p, x in zip(ps, xs))


class _Rules:
    def __init__(self, rules: dict):
        def split(d):
            return [tuple(k.rpartition("@")[::2]) for k in d]
        self.attrs = split(rules.get("attrs", {}))
        self.path_attrs = split(rules.get("path_attrs", {}))
        self.datasets = list(rules.get("datasets", {}))
        self.path_datasets = list(rules.get("path_datasets", {}))
        self.reason = {**{("attr", k): v for k, v in rules.get("attrs", {}).items()},
                       **{("path attr", k): v for k, v in rules.get("path_attrs", {}).items()},
                       **{("dataset", k): v for k, v in rules.get("datasets", {}).items()},
                       **{("path dataset", k): v
                          for k, v in rules.get("path_datasets", {}).items()}}

    @staticmethod
    def _hit(pairs, path, name):
        return next((_akey(p, k) for p, k in pairs
                     if _match(p, path) and fnmatch.fnmatchcase(name, k)), None)

    def attr(self, path, name):
        """("attr" | "path attr" | None, rule key)."""
        hit = self._hit(self.attrs, path, name)
        if hit:
            return "attr", hit
        hit = self._hit(self.path_attrs, path, name)
        return ("path attr", hit) if hit else (None, None)

    def dataset(self, path):
        hit = next((p for p in self.datasets if _match(p, path)), None)
        if hit:
            return "dataset", hit
        hit = next((p for p in self.path_datasets if _match(p, path)), None)
        return ("path dataset", hit) if hit else (None, None)


# ------------------------------------------------------------------------------- hashing
def _md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def basename_md5(value) -> str:
    """md5 of the basenames of a path-valued dataset or attribute (shape kept, directories
    dropped).  The one helper for both ``PATH_DATASETS`` and ``PATH_ATTRS``."""
    a = np.asarray(value)
    names = [posixpath.basename(x.decode("utf-8", "surrogateescape") if isinstance(x, bytes)
                                else str(x)) for x in a.ravel().tolist()]
    return _md5(json.dumps({"shape": list(a.shape), "basenames": names}).encode())


def _float_kind(dtype) -> bool:
    return dtype.kind == "f" and dtype.itemsize in _UINT


def _file_entry(path: Path, rules: _Rules) -> dict:
    datasets, attrs = {}, {}
    with h5py.File(path, "r") as f:
        objs = [("/", f)]
        f.visititems(lambda n, o: objs.append(("/" + n, o)))
        for p, o in sorted(objs, key=lambda t: t[0]):
            for k in sorted(o.attrs.keys()):
                what, _ = rules.attr(p, k)
                v = o.attrs[k]
                attrs[_akey(p, k)] = (basename_md5(v) if what == "path attr"
                                      else _md5(_canon_attr(v)))
            if not isinstance(o, h5py.Dataset):
                continue
            what, _ = rules.dataset(p)
            if what == "path dataset":
                md5, kind = basename_md5(o[()]), "exact"
            else:
                md5 = dataset_md5(o)
                kind = ("provenance" if what == "dataset"
                        else "float" if _float_kind(o.dtype) else "exact")
            datasets[p] = {"dtype": o.dtype.str, "shape": list(o.shape), "md5": md5,
                           "kind": kind}
    return {"datasets": dict(sorted(datasets.items())), "attrs": dict(sorted(attrs.items()))}


def _files(target) -> dict[str, Path]:
    """{relative name: path}.  ``target`` is a directory, or a mapping {role: directory} whose
    files are named ``<role>/<path in the directory>`` (so a reference built from directories
    laid out differently on another machine names its files the same way)."""
    roots = ({str(r): Path(d) for r, d in target.items()} if isinstance(target, Mapping)
             else {"": Path(target)})
    out = {}
    for role, d in sorted(roots.items()):
        if not d.is_dir():
            raise RegressError(f"{d} is not a directory")
        for p in list_h5(d):
            rel = p.relative_to(d).as_posix()
            out[f"{role}/{rel}" if role else rel] = p
    return dict(sorted(out.items()))


def regression_manifest(target, rules: dict | None = None, log=None) -> dict:
    """Regression manifest of the HDF5 files under ``target`` (a directory, or {role: dir}).
    ``rules`` (the JSON form of ``exclusion_rules()``) defaults to this code's rules.
    Deterministic: every mapping is built in sorted key order."""
    rules = exclusion_rules() if rules is None else rules
    r = _Rules(rules)
    files = {}
    for rel, p in _files(target).items():
        if log:
            log(f"regression manifest {rel}")
        files[rel] = _file_entry(p, r)
    return {"excluded": {k: dict(sorted(v.items())) for k, v in sorted(rules.items())},
            "files": files}


# --------------------------------------------------------------------------- ULP distance
_UINT = {2: np.uint16, 4: np.uint32, 8: np.uint64}
_INT64_MAX = np.iinfo(np.int64).max


def _ordered(x: np.ndarray) -> np.ndarray:
    """Unsigned integers in the order of the floats: -0.0 and +0.0 one apart, every adjacent
    pair of floats one apart.  Equivalent to k = -m - 1 for negative floats and k = m for
    non-negative ones (m = bits without the sign), shifted by 2**(w-1) so it fits unsigned."""
    u = _UINT[x.dtype.itemsize]
    bits = x.view(u)
    sign = u(1) << u(8 * x.dtype.itemsize - 1)
    mag = bits & (sign - u(1))
    return np.where((bits & sign) != 0, (sign - u(1)) - mag, sign + mag).astype(u)


def ulp_distance(a, b) -> np.ndarray:
    """Element-wise distance in units in the last place of the stored dtype (int64).

    Both arrays must have the same float dtype (float16/32/64) and the same shape.  The
    distance counts the representable values between the two (|-0.0 vs +0.0| = 1).  It is
    computed on unsigned integers, so it never overflows; a float64 distance above 2**63 - 1
    (only between values of opposite sign far from zero) saturates at 2**63 - 1.  NaN is not
    special here: callers decide (``compare`` requires identical bits)."""
    a, b = np.asarray(a), np.asarray(b)
    if a.dtype.newbyteorder("=") != b.dtype.newbyteorder("="):
        raise TypeError(f"ulp_distance: dtype mismatch {a.dtype} vs {b.dtype}")
    if not _float_kind(a.dtype):
        raise TypeError(f"ulp_distance: not a float16/32/64 dtype: {a.dtype}")
    if a.shape != b.shape:
        raise ValueError(f"ulp_distance: shape mismatch {a.shape} vs {b.shape}")
    native = a.dtype.newbyteorder("=")
    oa = _ordered(a.astype(native, copy=False))
    ob = _ordered(b.astype(native, copy=False))
    d = np.where(oa >= ob, oa - ob, ob - oa)
    if d.dtype == np.uint64:
        d = np.minimum(d, np.uint64(_INT64_MAX))
    return d.astype(np.int64)


# -------------------------------------------------------------------------------- report
@dataclass
class RegressReport:
    passed: bool
    n_datasets_exact: int
    ulp_diffs: list[dict] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        out = ["# Regression report", "", f"RESULT: {'PASS' if self.passed else 'FAIL'}", "",
               f"- Datasets identical (md5 of the bytes): {self.n_datasets_exact}",
               (f"- Float datasets that differ by at most {ULP_TOLERANCE} ULP: "
                f"{len(self.ulp_diffs)}"),
               f"- Failures: {len(self.failures)}", ""]
        out += [f"## Float datasets within {ULP_TOLERANCE} ULP", ""]
        if self.ulp_diffs:
            out += ["| file | dataset | elements differing | maximum |", "|---|---|---|---|"]
            out += [f"| {d['file']} | {d['dataset']} | {d['n_differ']} | {d['max_ulp']} ULP |"
                    for d in self.ulp_diffs]
        else:
            out.append("none")
        out += ["", "## Failures", ""]
        out += [f"- {m}" for m in self.failures] or ["none"]
        out += ["", "## Excluded or compared by basename (declared rules)", ""]
        out += [f"- {m}" for m in self.excluded] or ["none"]
        return "\n".join(out) + "\n"


# ------------------------------------------------------------------------------- compare
def _ulp_compare(rel, p, rentry, tpath, ref_arrays):
    """(ulp_diff dict or None, list of failures) for one float dataset whose md5 differs."""
    need = f"{rel}: dataset {p}: float values differ (md5); arrays needed for ULP comparison"
    if ref_arrays is None:
        return None, [f"{need} (no reference arrays given)"]
    rp = Path(ref_arrays) / rel
    if not rp.is_file():
        return None, [f"{need} ({rp} not found)"]
    n_differ = n_nan = max_ulp = 0
    h = hashlib.md5()
    with h5py.File(rp, "r") as fr, h5py.File(tpath, "r") as ft:
        if p not in fr:
            return None, [f"{rel}: dataset {p}: not in the reference arrays {rp}"]
        a, b = fr[p], ft[p]
        if a.dtype != b.dtype or a.shape != b.shape:
            return None, [(f"{rel}: dataset {p}: reference arrays {rp} have dtype/shape "
                           f"{a.dtype}{a.shape}, target {b.dtype}{b.shape}")]
        for ba, bb in zip(_row_blocks(a), _row_blocks(b)):
            ba, bb = np.ascontiguousarray(ba), np.ascontiguousarray(bb)
            h.update(ba.tobytes())
            d = ulp_distance(ba, bb)
            nan = (np.isnan(ba) | np.isnan(bb)) & (d != 0)
            nz = (d != 0) & ~nan
            n_nan += int(nan.sum())
            n_differ += int(nz.sum())
            if nz.any():
                max_ulp = max(max_ulp, int(d[nz].max()))
    if h.hexdigest() != rentry["md5"]:
        return None, [(f"{rel}: dataset {p}: the reference arrays {rp} do not match the "
                       f"reference manifest (md5 {h.hexdigest()} != {rentry['md5']})")]
    bad = []
    if n_nan:
        bad.append(f"{rel}: dataset {p}: {n_nan} element(s) with a NaN whose bits differ from "
                   f"the reference (NaN must match bit for bit)")
    if max_ulp > ULP_TOLERANCE:
        bad.append(f"{rel}: dataset {p}: {n_differ} element(s) differ, max {max_ulp} ULP "
                   f"(tolerance {ULP_TOLERANCE} ULP)")
    if bad or n_differ == 0:
        return None, bad
    return {"file": rel, "dataset": p, "n_differ": n_differ, "max_ulp": max_ulp}, []


def compare(reference: dict, target, ref_arrays: Path | None) -> RegressReport:
    """Compare the HDF5 files under ``target`` (directory or {role: dir}) with a reference
    regression manifest; ``ref_arrays/<rel>`` holds the reference files for the ULP check."""
    failures: list[str] = []
    rules = reference.get("excluded")
    if rules != exclusion_rules():
        failures.append("the exclusion rules of the reference differ from this code's exclusion "
                        "rules: rebuild the reference or restore the rules "
                        f"(reference: {json.dumps(rules, sort_keys=True)})")
        rules = rules if isinstance(rules, dict) else exclusion_rules()
    r = _Rules(rules)
    paths = _files(target)
    tman = regression_manifest(target, rules=rules)
    rf, tf = reference.get("files", {}), tman["files"]
    if not rf:
        failures.append("the reference lists no files")
    failures += [f"{rel}: file missing from the target" for rel in sorted(set(rf) - set(tf))]
    failures += [f"{rel}: file not in the reference" for rel in sorted(set(tf) - set(rf))]
    n_exact = 0
    ulp_diffs = []
    seen: dict[tuple[str, str], list[int]] = {}     # (what, rule key) -> [n_seen, n_differ]

    def note(what, key, differs):
        c = seen.setdefault((what, key), [0, 0])
        c[0] += 1
        c[1] += int(differs)

    for rel in sorted(set(rf) & set(tf)):
        rd, td = rf[rel]["datasets"], tf[rel]["datasets"]
        failures += [f"{rel}: dataset {p} missing from the target" for p in sorted(set(rd) - set(td))]
        failures += [f"{rel}: dataset {p} not in the reference" for p in sorted(set(td) - set(rd))]
        for p in sorted(set(rd) & set(td)):
            a, b = rd[p], td[p]
            bad = [f"{fld} {a[fld]} != {b[fld]}" for fld in ("kind", "dtype", "shape")
                   if a[fld] != b[fld]]
            if bad:
                failures.append(f"{rel}: dataset {p}: " + "; ".join(bad))
                continue
            what, key = r.dataset(p)
            if what:
                note(what, key, a["md5"] != b["md5"])
            if a["kind"] == "provenance":
                continue
            if a["md5"] == b["md5"]:
                n_exact += 1
            elif a["kind"] == "float":
                d, bad = _ulp_compare(rel, p, a, paths[rel], ref_arrays)
                failures += bad
                if d:
                    ulp_diffs.append(d)
            else:
                how = "basenames differ" if what == "path dataset" else "values differ"
                failures.append(f"{rel}: dataset {p}: {how} ({a['kind']}: md5 {a['md5']} != "
                                f"{b['md5']})")
        ra, ta = rf[rel]["attrs"], tf[rel]["attrs"]
        failures += [f"{rel}: attribute {k} missing from the target" for k in sorted(set(ra) - set(ta))]
        failures += [f"{rel}: attribute {k} not in the reference" for k in sorted(set(ta) - set(ra))]
        for k in sorted(set(ra) & set(ta)):
            p, _, name = k.rpartition("@")
            what, key = r.attr(p, name)
            if what == "attr":
                note(what, key, ra[k] != ta[k])
                continue
            if what == "path attr":
                note(what, key, ra[k] != ta[k])
            if ra[k] != ta[k]:
                how = "basenames differ" if what == "path attr" else "value differs"
                failures.append(f"{rel}: attribute {k}: {how} (md5 {ra[k]} != {ta[k]})")

    excluded = []
    for (what, key), (n, nd) in sorted(seen.items()):
        reason = r.reason.get((what, key), "")
        if what in ("attr", "dataset"):
            excluded.append(f"{what} {key}: value ignored in {n} occurrence(s), differs in {nd} "
                            f"({reason})")
        else:
            excluded.append(f"{what} {key}: compared by basename in {n} occurrence(s), full "
                            f"values not compared, basenames differ in {nd} ({reason})")
    return RegressReport(passed=not failures, n_datasets_exact=n_exact, ulp_diffs=ulp_diffs,
                         failures=failures, excluded=excluded)


# --------------------------------------------------------------------------- run_regress
def reference_json(reference: str) -> Path:
    if reference not in REFERENCE_STEMS:
        raise ValueError(f"unknown regression reference {reference!r} "
                         f"(one of {', '.join(REFERENCE_STEMS)})")
    return REGRESS_DIR / f"{REFERENCE_STEMS[reference]}.json"


def load_reference(reference: str) -> dict:
    p = reference_json(reference)
    if not p.is_file():
        stem = REFERENCE_STEMS[reference]
        raise RegressError(f"{p} does not exist: the {reference} regression reference is built "
                           f"in {REFERENCE_TASKS[stem]} of the build plan; there is nothing to "
                           f"compare against until it is committed")
    return json.loads(p.read_text())


def _stages_pixel(s, pix, scratch: Path, log) -> dict[str, Path]:
    """The calls of `dgs extract --pixel N`, `dgs package`, `dgs manifest` and `dgs verify`."""
    from .driver import _run_jobs
    from .manifest import write_manifest
    from .package import package
    from .shard import run_pixel_job
    from .verify import verify
    shard_dir, chunks = scratch / "shards", scratch / "chunks"
    shard_dir.mkdir(parents=True)
    job = {"pix": int(pix), "shard_dir": str(shard_dir), "staging": str(scratch / "staging"),
           "keep_temp": False, "local_flat": None, "redrock_dir": None,
           "raw_root": str(s.raw_root), "fetch": False}
    failures = _run_jobs([job], 1, shard_dir / "extract_log.jsonl", run_pixel_job, log)
    if failures:
        raise RegressError(f"pixel {pix}: extract failed: {failures[0][1]} "
                           f"(see {shard_dir / 'extract_log.jsonl'})")
    package(shard_dir, chunks, with_resolution=s.with_resolution, log=log)
    write_manifest(chunks, chunks / "manifest.json", log=log)
    rep = verify(chunks, chunks / "manifest.json", shard_dir=shard_dir, log=log)
    (scratch / "VERIFY.txt").write_text(rep.text() + "\n")
    if not rep.ok:
        raise RegressError(f"pixel {pix}: verify failed; see {scratch / 'VERIFY.txt'}")
    return {"shards": shard_dir, "chunks": chunks}


def _stages_batch(s, batch_id, scratch: Path, log) -> dict[str, Path]:
    """``run_batch`` on a copy of the settings whose outputs live in ``scratch``; the raw files
    are read from ``s.raw_root`` and nothing is deleted."""
    s2 = dataclasses.replace(s, shard_root=scratch / "shard_root",
                             batch_root=scratch / "batch_root", staging=scratch / "staging",
                             keep_raw_after_verify=True, keep_shards_after_verify=True)
    stage = run_batch(s2, batch_id, fetch=False, until="verified", log=log)
    if stage != "verified":
        raise RegressError(f"batch {batch_id}: reached {stage!r}, not 'verified'")
    shard_dir, out = batch_dirs(s2, batch_id)
    return {"shards": shard_dir, "chunks": out}


def run_regress(s, reference: str, scratch: Path, log=print) -> RegressReport:
    """Run the stages for ``reference`` ("21852", "batch000" or "ref22") into ``scratch`` (absent
    or empty), compare shards and chunks with ``reference/regress/<stem>.json`` and the arrays
    under ``s.reference_root/<stem>/``; write ``REGRESS.md`` and ``regress.json`` in ``scratch``."""
    ref_path = reference_json(reference)
    ref = load_reference(reference)
    stem = REFERENCE_STEMS[reference]
    scratch = Path(scratch)
    if scratch.exists() and (not scratch.is_dir() or any(scratch.iterdir())):
        raise RegressError(f"{scratch}: the scratch directory must be absent or empty (outputs "
                           f"left there would be reused instead of recomputed)")
    scratch.mkdir(parents=True, exist_ok=True)
    if reference == "21852":
        target = _stages_pixel(s, PIXEL_21852, scratch, log)
    else:
        target = _stages_batch(s, BATCH_OF_REFERENCE[reference], scratch, log)
    arrays = Path(s.reference_root) / stem if s.reference_root is not None else None
    log(f"regress {reference}: comparing with {ref_path}")
    rep = compare(ref, target, arrays)
    (scratch / "REGRESS.md").write_text(rep.to_markdown())
    record = {
        "reference": reference, "reference_json": str(ref_path),
        "reference_arrays": None if arrays is None else str(arrays),
        "targets": {k: str(v) for k, v in sorted(target.items())},
        "with_resolution": bool(s.with_resolution), "kit_version": config.KIT_VERSION,
        "lockfile_sha256": config.lockfile_sha256(), "machine": platform.machine(),
        "python_version": platform.python_version(), "numpy_version": np.__version__,
        "h5py_version": h5py.__version__,
        "written": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        **dataclasses.asdict(rep),
    }
    (scratch / "regress.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    log(f"regress {reference}: {'PASS' if rep.passed else 'FAIL'}; written {scratch / 'REGRESS.md'}")
    return rep
