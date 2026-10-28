"""Independent verifier for kit gate checks 1 and 2 (pixel 21852). h5py + numpy only; no kit code.
Read-only on all three files.

CHECK 1: temp v4-layout file vs reference, exact (full recursive walk: every group, dataset,
         attribute, dtype, shape, storage layout). Exclusions: metadata attr creation_date;
         directory part of catalog/coadd_file (basename must match).
CHECK 2: shard per-class stacked rows vs reference per-object groups. Mapping derived from the
         files: ref obj_NNNNN <-> ref catalog row NNNNN (checked via targetid attr), shard row j
         <-> shard catalog row j (checked via spectra_attrs/targetid); rows matched on
         (class, targetid, program); bijection asserted.
Usage: python -I compare_gate_1to2.py REF.h5 V4LAYOUT.h5 SHARD.h5 OUT.json
"""
import sys, os, json, time, hashlib
import h5py, numpy as np

CLASSES = ["BGS", "ELG", "LRG"]
SPEC = ["flux", "ivar", "mask", "resolution"]
EXCL_ATTR = {("metadata", "creation_date")}
BASENAME_DSETS = {"catalog/coadd_file"}  # suffix match on path


def md5(fn):
    h = hashlib.md5()
    with open(fn, "rb") as f:
        for b in iter(lambda: f.read(1 << 24), b""):
            h.update(b)
    return h.hexdigest()


def arr_equal(a, b):
    """Exact equality: same dtype, same shape, same raw bytes (object/vlen: elementwise ==)."""
    a = np.asarray(a); b = np.asarray(b)
    if a.dtype != b.dtype or a.shape != b.shape:
        return False
    if a.dtype.kind == "O":
        return all(x == y for x, y in zip(a.ravel().tolist(), b.ravel().tolist()))
    return np.array_equal(np.ascontiguousarray(a).view(np.uint8).ravel(),
                          np.ascontiguousarray(b).view(np.uint8).ravel())


def n_elem_diff(a, b):
    a = np.ascontiguousarray(a); b = np.ascontiguousarray(b)
    if a.dtype != b.dtype or a.shape != b.shape:
        return -1
    if a.dtype.kind == "O":
        return int(sum(x != y for x, y in zip(a.ravel().tolist(), b.ravel().tolist())))
    isz = a.dtype.itemsize
    va = a.view(np.uint8).reshape(-1, isz); vb = b.view(np.uint8).reshape(-1, isz)
    return int((va != vb).any(axis=1).sum())


def attr_equal(x, y):
    if type(x) is not type(y):
        return False
    if isinstance(x, np.ndarray) or isinstance(x, np.generic):
        return arr_equal(x, y)
    return x == y


def storage(d):
    return {"dtype": str(d.dtype), "shape": list(d.shape), "chunks": d.chunks, "compression": d.compression,
            "compression_opts": d.compression_opts, "shuffle": d.shuffle, "fletcher32": d.fletcher32,
            "scaleoffset": d.scaleoffset, "fillvalue": str(d.fillvalue), "filters": sorted(d._filters.keys())}


def basename_strip(a):
    return np.array([os.path.basename(x) for x in a.tolist()], dtype=a.dtype)


# ---------------------------------------------------------------- CHECK 1
def check1(fr, fv):
    R = {"only_ref": [], "only_v4": [], "type_mismatch": [], "attr_diffs": [], "attrs_compared": 0,
         "attrs_excluded": [], "dset_diffs": [], "storage_diffs": [], "datasets_compared": 0,
         "elements_compared": 0, "elements_differing": 0, "groups_compared": 0,
         "basename_rule_applied": [], "storage_summary": {}, "per_class_objects": {}}
    paths_r, paths_v = {}, {}
    fr.visititems(lambda n, o: paths_r.__setitem__(n, o)); fv.visititems(lambda n, o: paths_v.__setitem__(n, o))
    paths_r[""] = fr; paths_v[""] = fv
    R["only_ref"] = sorted(set(paths_r) - set(paths_v)); R["only_v4"] = sorted(set(paths_v) - set(paths_r))
    for p in sorted(set(paths_r) & set(paths_v)):
        a, b = paths_r[p], paths_v[p]
        if isinstance(a, h5py.Dataset) != isinstance(b, h5py.Dataset):
            R["type_mismatch"].append(p); continue
        # attributes
        ka, kb = set(a.attrs), set(b.attrs)
        for k in sorted(ka ^ kb):
            R["attr_diffs"].append({"path": p, "attr": k, "issue": "present in one file only",
                                    "in_ref": k in ka})
        for k in sorted(ka & kb):
            if (p, k) in EXCL_ATTR:
                R["attrs_excluded"].append({"path": p, "attr": k, "ref": str(a.attrs[k]), "v4": str(b.attrs[k])})
                continue
            R["attrs_compared"] += 1
            if not attr_equal(a.attrs[k], b.attrs[k]):
                R["attr_diffs"].append({"path": p, "attr": k, "ref": str(a.attrs[k])[:200], "v4": str(b.attrs[k])[:200]})
        if isinstance(a, h5py.Group):
            R["groups_compared"] += 1
            continue
        # storage
        sa, sb = storage(a), storage(b)
        if sa != sb:
            R["storage_diffs"].append({"path": p, "ref": sa, "v4": sb})
        leaf = p.split("/")[-1] if "/spectra/obj_" in p else "/".join(p.split("/")[1:])
        key = ("spectra/obj_*/" + leaf) if "/spectra/obj_" in p else p
        ss = R["storage_summary"].setdefault(key if "/spectra/obj_" in p else "tables", {})
        sig = json.dumps({k: v for k, v in sa.items() if k != "shape"}, default=str) if "/spectra/obj_" in p else \
            json.dumps({"chunks": sa["chunks"], "compression": sa["compression"], "filters": sa["filters"]}, default=str)
        ss[sig] = ss.get(sig, 0) + 1
        x, y = a[()], b[()]
        R["datasets_compared"] += 1
        R["elements_compared"] += int(np.asarray(x).size)
        if any(p.endswith(s) for s in BASENAME_DSETS):
            same_full = arr_equal(x, y)
            xb, yb = basename_strip(x), basename_strip(y)
            ok = arr_equal(xb, yb)
            R["basename_rule_applied"].append({"path": p, "full_path_equal": bool(same_full), "basename_equal": bool(ok),
                                               "ref_example": x[0].decode(), "v4_example": y[0].decode()})
            if not ok:
                R["dset_diffs"].append({"path": p, "n_diff": n_elem_diff(xb, yb), "note": "basename differs"})
                R["elements_differing"] += max(n_elem_diff(xb, yb), 0)
            continue
        if not arr_equal(x, y):
            nd = n_elem_diff(x, y)
            R["dset_diffs"].append({"path": p, "n_diff": nd, "dtype_ref": str(np.asarray(x).dtype),
                                    "dtype_v4": str(np.asarray(y).dtype), "shape_ref": list(np.shape(x)),
                                    "shape_v4": list(np.shape(y))})
            R["elements_differing"] += max(nd, 0)
    for c in CLASSES:
        R["per_class_objects"][c] = {"ref": len(fr[c]["spectra"]), "v4": len(fv[c]["spectra"])}
    R["PASS"] = not (R["only_ref"] or R["only_v4"] or R["type_mismatch"] or R["attr_diffs"] or
                     R["dset_diffs"] or R["storage_diffs"])
    return R


# ---------------------------------------------------------------- CHECK 2
def check2(fr, fs):
    R = {"mapping": {}, "spectra": {d: {"rows_compared": 0, "rows_differing": 0, "elements_compared": 0,
                                        "elements_differing": 0, "dtype_ref": None, "dtype_shard": None}
                                    for d in SPEC + ["wavelength"]},
         "spectra_attrs": {}, "columns": {}, "class_attrs": {}, "metadata": {}, "shard_only_keys": [],
         "ref_only_keys": [], "photsys": {}, "root_attrs": {}, "storage": {}, "datasets_compared": 0,
         "elements_compared": 0, "elements_differing": 0}
    # key inventory (structure outside per-object spectra)
    def keys(f, skip_obj):
        out = []
        f.visit(lambda n: out.append(n) if not (skip_obj and "/spectra/obj_" in n) else None)
        return set(out)
    kr, ks = keys(fr, True), keys(fs, False)
    R["shard_only_keys"] = sorted(ks - kr); R["ref_only_keys"] = sorted(kr - ks)
    R["root_attrs"] = {k: (fs.attrs[k].tolist() if isinstance(fs.attrs[k], np.ndarray) else
                           (fs.attrs[k].item() if isinstance(fs.attrs[k], np.generic) else fs.attrs[k]))
                       for k in fs.attrs}
    R["root_attrs"] = json.loads(json.dumps(R["root_attrs"], default=lambda o: o.decode() if isinstance(o, bytes) else str(o)))
    R["ref_root_attrs"] = list(fr.attrs)
    # metadata attrs + units
    for g in ["metadata", "metadata/units"]:
        a, b = fr[g].attrs, fs[g].attrs
        diffs, excl = [], []
        for k in sorted(set(a) | set(b)):
            if (g, k) in EXCL_ATTR:
                excl.append(k); continue
            if k not in a or k not in b or not attr_equal(a[k], b[k]):
                diffs.append(k)
        R["metadata"][g] = {"n_attrs": len(set(a) | set(b)), "diffs": diffs, "excluded": excl}
    wl_root = fs["wavelength"][()]
    R["storage"]["root_wavelength"] = storage(fs["wavelength"])
    for c in CLASSES:
        gr, gs = fr[c], fs[c]
        ar, as_ = gr.attrs, gs.attrs
        R["class_attrs"][c] = {"n": len(set(ar) | set(as_)),
                               "diffs": [k for k in sorted(set(ar) | set(as_))
                                         if k not in ar or k not in as_ or not attr_equal(ar[k], as_[k])]}
        tid_r = gr["catalog/targetid"][()]; prog_r = gr["catalog/program"][()]
        tid_s = gs["catalog/targetid"][()]; prog_s = gs["catalog/program"][()]
        nobj = len(gr["spectra"])
        assert nobj == tid_r.size, (c, nobj, tid_r.size)
        # ref: obj_i <-> catalog row i
        bad_r = sum(int(gr["spectra"][f"obj_{i:05d}"].attrs["targetid"]) != int(tid_r[i]) for i in range(nobj))
        assert sorted(gr["spectra"].keys()) == [f"obj_{i:05d}" for i in range(nobj)]
        sa_tid = gs["spectra_attrs/targetid"][()]
        bad_s = int((sa_tid != tid_s).sum())
        key_r = {(int(t), p.decode()): i for i, (t, p) in enumerate(zip(tid_r, prog_r))}
        key_s = {(int(t), p.decode()): j for j, (t, p) in enumerate(zip(tid_s, prog_s))}
        assert len(key_r) == nobj, f"{c}: ref keys not unique"
        assert len(key_s) == tid_s.size, f"{c}: shard keys not unique"
        perm = np.array([key_s.get(k, -1) for k in sorted(key_r, key=key_r.get)])
        bij = (set(key_r) == set(key_s)) and (perm >= 0).all() and np.unique(perm).size == nobj \
            and tid_s.size == nobj and gs["spectra/flux"].shape[0] == nobj
        assert bij, f"{c}: mapping is not a bijection"
        progs = {}
        for (_, p) in key_r:
            progs[p] = progs.get(p, 0) + 1
        R["mapping"][c] = {"n_ref_objects": nobj, "n_shard_rows": int(tid_s.size), "bijection": bool(bij),
                           "identity_permutation": bool((perm == np.arange(nobj)).all()),
                           "ref_objattr_targetid_vs_catalog_mismatch": int(bad_r),
                           "shard_spectra_attrs_targetid_vs_catalog_mismatch": bad_s, "programs": progs}
        # table columns: ref row i vs shard row perm[i]
        for sub in ["catalog", "photometry", "quality"]:
            for col in sorted(set(gr[sub]) | set(gs[sub])):
                key = f"{c}/{sub}/{col}"
                if col not in gr[sub] or col not in gs[sub]:
                    R["columns"][key] = {"present_ref": col in gr[sub], "present_shard": col in gs[sub]}
                    continue
                x = gr[sub][col][()]; y = gs[sub][col][()][perm]
                note = None
                if f"{sub}/{col}" in BASENAME_DSETS:
                    note = {"full_path_equal": bool(arr_equal(x, y)), "ref_example": x[0].decode(),
                            "shard_example": y[0].decode()}
                    x, y = basename_strip(x), basename_strip(y)
                eq = arr_equal(x, y); nd = 0 if eq else n_elem_diff(x, y)
                R["columns"][key] = {"equal": bool(eq), "n_diff": nd, "dtype": str(x.dtype),
                                     "storage_equal": storage(gr[sub][col]) == storage(gs[sub][col])}
                if note: R["columns"][key]["basename_rule"] = note
                R["datasets_compared"] += 1; R["elements_compared"] += int(x.size); R["elements_differing"] += max(nd, 0)
        # spectra_attrs vs object attrs
        objs = [gr["spectra"][f"obj_{i:05d}"] for i in range(nobj)]
        anames = sorted(set().union(*[set(o.attrs) for o in objs]))
        for an in sorted(set(anames) | set(gs["spectra_attrs"])):
            key = f"{c}/{an}"
            if an not in gs["spectra_attrs"] or an not in anames:
                R["spectra_attrs"][key] = {"present_ref": an in anames, "present_shard": an in gs["spectra_attrs"]}
                continue
            sv = gs["spectra_attrs"][an][()][perm]
            vals = [o.attrs[an] for o in objs]
            rtypes = sorted({type(v).__name__ + ":" + str(getattr(v, "dtype", "")) for v in vals})
            rv = np.array(vals)
            eq = arr_equal(rv, sv)
            R["spectra_attrs"][key] = {"equal": bool(eq), "n_diff": 0 if eq else n_elem_diff(rv.astype(sv.dtype), sv),
                                       "ref_attr_types": rtypes, "shard_dtype": str(sv.dtype)}
            R["datasets_compared"] += 1; R["elements_compared"] += int(sv.size)
            R["elements_differing"] += 0 if eq else max(R["spectra_attrs"][key]["n_diff"], 0)
        # spectra
        stack = {d: gs["spectra"][d][()] for d in SPEC}
        R["storage"][c] = {d: storage(gs["spectra"][d]) for d in SPEC}
        for i in range(nobj):
            o = objs[i]; j = perm[i]
            for d in SPEC:
                x = o[d][()]; y = stack[d][j]
                S = R["spectra"][d]
                S["dtype_ref"], S["dtype_shard"] = str(x.dtype), str(y.dtype)
                S["rows_compared"] += 1; S["elements_compared"] += int(x.size)
                if not arr_equal(x, y):
                    S["rows_differing"] += 1; S["elements_differing"] += max(n_elem_diff(x, y), 0)
            x = o["wavelength"][()]
            S = R["spectra"]["wavelength"]; S["dtype_ref"], S["dtype_shard"] = str(x.dtype), str(wl_root.dtype)
            S["rows_compared"] += 1; S["elements_compared"] += int(x.size)
            if not arr_equal(x, wl_root):
                S["rows_differing"] += 1; S["elements_differing"] += max(n_elem_diff(x, wl_root), 0)
        # photsys
        ps = gs["catalog/photsys"][()] if "photsys" in gs["catalog"] else None
        if ps is not None:
            u, n = np.unique(ps, return_counts=True)
            R["photsys"][c] = {"dtype": str(ps.dtype), "counts": {k.decode(): int(v) for k, v in zip(u, n)},
                               "subset_of_N_S_G": bool(set(k.decode() for k in u) <= {"N", "S", "G"})}
        del stack
    for d, S in R["spectra"].items():
        R["datasets_compared"] += S["rows_compared"]; R["elements_compared"] += S["elements_compared"]
        R["elements_differing"] += S["elements_differing"]
    allowed_extra = {"wavelength"} | {f"{c}/{x}" for c in CLASSES for x in
                     ["catalog/photsys", "spectra/flux", "spectra/ivar", "spectra/mask", "spectra/resolution",
                      "spectra_attrs"] + [f"spectra_attrs/{a}" for a in
                     ["arm_B_coverage", "arm_R_coverage", "arm_Z_coverage", "extraction_success", "n_pixels",
                      "resolution_n_diag", "snr_median", "targetid"]]}
    R["unexpected_shard_only_keys"] = sorted(set(R["shard_only_keys"]) - allowed_extra)
    R["PASS"] = (all(m["bijection"] for m in R["mapping"].values())
                 and all(S["rows_differing"] == 0 for S in R["spectra"].values())
                 and all(v.get("equal", k.endswith("/catalog/photsys")) for k, v in R["columns"].items())
                 and all(v.get("equal", False) for v in R["spectra_attrs"].values())
                 and all(not v["diffs"] for v in R["class_attrs"].values())
                 and all(not v["diffs"] for v in R["metadata"].values())
                 and not R["ref_only_keys"] and not R["unexpected_shard_only_keys"]
                 and all(m["ref_objattr_targetid_vs_catalog_mismatch"] == 0 and
                         m["shard_spectra_attrs_targetid_vs_catalog_mismatch"] == 0 for m in R["mapping"].values()))
    return R


# ---------------------------------------------------------------- sensitivity control
def flip_ulp(a, idx):
    b = a.copy()
    v = b.view(np.uint32)
    v.flat[idx] ^= np.uint32(1)  # last mantissa bit = 1 ULP for normal finite values
    return b


def sensitivity(fr, fv, fs):
    out = {}
    a = fr["ELG/spectra/obj_00010/flux"][()]
    idx = 3000
    b = flip_ulp(a, idx)
    out["ref_obj_flux_ELG_obj_00010"] = {"value_before": float(a[idx]), "value_after": float(b[idx]),
                                         "abs_delta": float(abs(np.float64(b[idx]) - np.float64(a[idx]))),
                                         "comparator_says_equal_unmodified": bool(arr_equal(a, fv["ELG/spectra/obj_00010/flux"][()])),
                                         "comparator_flags_1ulp": not arr_equal(a, b), "n_elem_diff": n_elem_diff(a, b)}
    row = fs["LRG/spectra/flux"][5]
    r2 = flip_ulp(row, idx)
    out["shard_row_flux_LRG_row5"] = {"value_before": float(row[idx]), "value_after": float(r2[idx]),
                                      "comparator_says_equal_unmodified": bool(arr_equal(fr["LRG/spectra/obj_00005/flux"][()], row)),
                                      "comparator_flags_1ulp": not arr_equal(fr["LRG/spectra/obj_00005/flux"][()], r2),
                                      "n_elem_diff": n_elem_diff(fr["LRG/spectra/obj_00005/flux"][()], r2)}
    return out


def main(ref_fn, v4_fn, shard_fn, out_fn):
    t0 = time.time()
    out = {"files": {"ref": ref_fn, "v4layout": v4_fn, "shard": shard_fn},
           "ref_md5": md5(ref_fn), "ref_md5_expected": "20a4a9148dc13ccb0a9ca2a8ebb28b2d"}
    out["ref_md5_ok"] = out["ref_md5"] == out["ref_md5_expected"]
    assert out["ref_md5_ok"]
    with h5py.File(ref_fn, "r") as fr, h5py.File(v4_fn, "r") as fv, h5py.File(shard_fn, "r") as fs:
        out["sensitivity"] = sensitivity(fr, fv, fs)
        out["check1"] = check1(fr, fv)
        out["check2"] = check2(fr, fs)
    out["elapsed_s"] = time.time() - t0
    with open(out_fn, "w") as f:
        json.dump(out, f, indent=1, default=str)
    c1, c2 = out["check1"], out["check2"]
    print(json.dumps({"md5_ok": out["ref_md5_ok"], "sensitivity": out["sensitivity"],
                      "check1": {k: c1[k] for k in ["PASS", "only_ref", "only_v4", "type_mismatch", "datasets_compared",
                                                    "elements_compared", "elements_differing", "groups_compared",
                                                    "attrs_compared", "attrs_excluded", "basename_rule_applied",
                                                    "per_class_objects", "storage_summary"]} |
                      {"n_attr_diffs": len(c1["attr_diffs"]), "attr_diffs_head": c1["attr_diffs"][:10],
                       "n_dset_diffs": len(c1["dset_diffs"]), "dset_diffs_head": c1["dset_diffs"][:10],
                       "n_storage_diffs": len(c1["storage_diffs"]), "storage_diffs_head": c1["storage_diffs"][:3]},
                      "check2": {k: c2[k] for k in ["PASS", "mapping", "spectra", "shard_only_keys", "ref_only_keys",
                                                    "unexpected_shard_only_keys", "photsys", "root_attrs", "metadata",
                                                    "class_attrs", "datasets_compared", "elements_compared",
                                                    "elements_differing"]} |
                      {"columns_not_equal": {k: v for k, v in c2["columns"].items() if not v.get("equal", False)},
                       "spectra_attrs": c2["spectra_attrs"],
                       "basename": {k: v["basename_rule"] for k, v in c2["columns"].items() if "basename_rule" in v},
                       "columns_storage_unequal": [k for k, v in c2["columns"].items() if v.get("storage_equal") is False]},
                      "elapsed_s": out["elapsed_s"]}, indent=1, default=str))


if __name__ == "__main__":
    main(*sys.argv[1:5])
