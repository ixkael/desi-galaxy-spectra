"""Independent byte-level comparison of the v4 reference extraction vs the desispec 0.70.0
re-extraction for HEALPix pixel 21852. Read-only on both files.
Usage: python -I compare_21852.py REF.h5 NEW.h5 OUT.json
"""
import sys, json, time
import h5py, numpy as np

HP = 21852
CLASSES = ["BGS", "ELG", "LRG"]
DSETS = ["flux", "ivar", "mask", "resolution", "wavelength"]
IGNORE_MD = {"creation_date", "n_coadds_processed", "n_total_objects"}
REGIONS = {  # Angstrom, approximate arm overlaps
    "B-only <5600": (0, 5600), "B/R overlap 5600-5950": (5600, 5950),
    "R-only 5950-7400": (5950, 7400), "R/Z overlap 7400-7700": (7400, 7700),
    "Z-only >7700": (7700, 1e9)}

def bytes_equal(a, b):
    return a.dtype == b.dtype and a.shape == b.shape and \
        np.array_equal(np.ascontiguousarray(a).view(np.uint8), np.ascontiguousarray(b).view(np.uint8))

def ulp_dist(a, b):
    """ULP distance between float32 arrays via ordered-int mapping."""
    ia = a.astype(np.float32).view(np.int32).astype(np.int64)
    ib = b.astype(np.float32).view(np.int32).astype(np.int64)
    ia = np.where(ia < 0, -(ia & 0x7FFFFFFF), ia)
    ib = np.where(ib < 0, -(ib & 0x7FFFFFFF), ib)
    return np.abs(ia - ib)

def attrs_dict(o):
    return {k: o.attrs[k] for k in o.attrs}

def same_val(a, b):
    a = np.atleast_1d(np.asarray(a)); b = np.atleast_1d(np.asarray(b))
    return a.dtype == b.dtype and a.shape == b.shape and \
        np.array_equal(a.view(np.uint8) if a.dtype.kind not in "OUS" else a,
                       b.view(np.uint8) if b.dtype.kind not in "OUS" else b)

def main(ref_fn, new_fn, out_fn):
    t0 = time.time()
    R = {"counts": {}, "datasets": {}, "storage": {}, "obj_attrs": {}, "columns": {},
         "class_attrs": {}, "metadata_attrs": {}, "units_attrs": {}, "nan": {}, "diff_examples": []}
    with h5py.File(ref_fn, "r") as fr, h5py.File(new_fn, "r") as fn:
        # metadata
        mr, mn = attrs_dict(fr["metadata"]), attrs_dict(fn["metadata"])
        for k in sorted(set(mr) | set(mn)):
            if k in IGNORE_MD:
                R["metadata_attrs"][k] = {"ignored": True, "ref": str(mr.get(k)), "new": str(mn.get(k))}
                continue
            eq = k in mr and k in mn and str(mr[k]) == str(mn[k]) and type(mr[k]) == type(mn[k])
            R["metadata_attrs"][k] = {"equal": bool(eq)} if eq else {"equal": False, "ref": str(mr.get(k)), "new": str(mn.get(k))}
        ur, un = attrs_dict(fr["metadata/units"]), attrs_dict(fn["metadata/units"])
        R["units_attrs"] = {k: bool(str(ur.get(k)) == str(un.get(k))) for k in sorted(set(ur) | set(un))}
        R["metadata_subgroups"] = {"ref": sorted(fr["metadata"].keys()), "new": sorted(fn["metadata"].keys())}

        for d in DSETS:
            R["datasets"][d] = {"n_compared": 0, "dtype_mismatch": 0, "shape_mismatch": 0,
                                "n_obj_byte_diff": 0, "n_elem_diff": 0, "n_elem_total": 0,
                                "max_abs": 0.0, "max_rel": 0.0, "max_ulp": 0, "ulp_hist": {},
                                "region_elem_diff": {r: 0 for r in REGIONS}, "nan_pattern_mismatch": 0,
                                "n_nan_ref": 0, "n_nan_new": 0}
            R["storage"][d] = {"compression_or_chunk_mismatch": 0}

        for c in CLASSES:
            gr, gn = fr[c], fn[c]
            hp_r = gr["catalog/healpix"][:]
            rows_r = np.where(hp_r == HP)[0]
            hp_n = gn["catalog/healpix"][:]
            rows_n = np.arange(hp_n.size)
            assert np.all(hp_n == HP), f"{c}: new file contains other pixels"
            # verify obj_i <-> catalog row i mapping in both files via targetid attr
            def key_map(g, rows):
                tid = g["catalog/targetid"][:]
                prog = g["catalog/program"][:]
                km, bad = {}, 0
                for i in rows:
                    o = g["spectra"][f"obj_{i:05d}"]
                    if int(o.attrs["targetid"]) != int(tid[i]):
                        bad += 1
                    k = (int(tid[i]), prog[i].decode())
                    assert k not in km, f"duplicate key {k}"
                    km[k] = i
                return km, bad
            km_r, bad_r = key_map(gr, rows_r)
            km_n, bad_n = key_map(gn, rows_n)
            sr, sn = set(km_r), set(km_n)
            matched = sorted(sr & sn)
            R["counts"][c] = {"ref_rows_hp21852": int(rows_r.size), "ref_rows_contiguous_from": int(rows_r.min()),
                              "ref_rows_contiguous": bool(rows_r.max() - rows_r.min() + 1 == rows_r.size),
                              "new_rows": int(rows_n.size), "matched": len(matched),
                              "only_ref": len(sr - sn), "only_new": len(sn - sr),
                              "objidx_targetid_mismatch_ref": bad_r, "objidx_targetid_mismatch_new": bad_n,
                              "n_spectra_groups_new": len(gn["spectra"]),
                              "same_row_order": [km_r[k] - rows_r.min() for k in matched] == [km_n[k] for k in matched],
                              "programs": {p: sum(1 for k in matched if k[1] == p) for p in sorted({k[1] for k in matched})}}
            ir = np.array([km_r[k] for k in matched]); inn = np.array([km_n[k] for k in matched])

            # table columns
            for sub in ["catalog", "photometry", "quality"]:
                cols = sorted(set(gr[sub]) | set(gn[sub]))
                for col in cols:
                    key = f"{c}/{sub}/{col}"
                    if col not in gr[sub] or col not in gn[sub]:
                        R["columns"][key] = {"present_both": False}; continue
                    a = gr[sub][col][int(rows_r.min()):int(rows_r.max()) + 1][ir - rows_r.min()]
                    b = gn[sub][col][:][inn]
                    eq = bytes_equal(a, b)
                    ent = {"equal": bool(eq), "dtype_ref": str(a.dtype), "dtype_new": str(b.dtype)}
                    if not eq and a.dtype.kind in "fiu" and a.shape == b.shape:
                        diff = ~((a == b) | (np.isnan(a) & np.isnan(b)) if a.dtype.kind == "f" else (a == b))
                        if diff.ndim > 1: diff = diff.any(axis=tuple(range(1, diff.ndim)))
                        ent["n_rows_diff"] = int(diff.sum())
                        ent["max_abs"] = float(np.nanmax(np.abs(a.astype(np.float64) - b.astype(np.float64))))
                        if a.dtype == np.float32:
                            ent["max_ulp"] = int(ulp_dist(a, b)[np.isfinite(a) & np.isfinite(b)].max())
                    elif not eq and a.shape == b.shape:
                        ent["n_rows_diff"] = int((a != b).sum())
                    R["columns"][key] = ent

            # class attrs
            ar, an = attrs_dict(gr), attrs_dict(gn)
            R["class_attrs"][c] = {k: {"ref": str(ar.get(k)), "new": str(an.get(k))} for k in sorted(set(ar) | set(an))}

            # per-object spectra + attrs
            oa = {}
            for k in matched:
                orr = gr["spectra"][f"obj_{km_r[k]:05d}"]
                onn = gn["spectra"][f"obj_{km_n[k]:05d}"]
                a1, a2 = attrs_dict(orr), attrs_dict(onn)
                for ak in set(a1) | set(a2):
                    s = oa.setdefault(ak, {"n": 0, "n_diff": 0, "max_abs": 0.0})
                    s["n"] += 1
                    if ak not in a1 or ak not in a2 or not same_val(a1[ak], a2[ak]):
                        s["n_diff"] += 1
                        try: s["max_abs"] = max(s["max_abs"], float(abs(np.float64(a1[ak]) - np.float64(a2[ak]))))
                        except Exception: pass
                if set(orr.keys()) != set(onn.keys()):
                    R.setdefault("dset_set_mismatch", []).append([c, *k])
                wl = onn["wavelength"][:]
                for d in DSETS:
                    S = R["datasets"][d]
                    dr, dn = orr[d], onn[d]
                    if (dr.compression, dr.compression_opts, dr.chunks, dr.shuffle) != (dn.compression, dn.compression_opts, dn.chunks, dn.shuffle):
                        R["storage"][d]["compression_or_chunk_mismatch"] += 1
                    a, b = dr[()], dn[()]
                    S["n_compared"] += 1
                    if a.dtype != b.dtype: S["dtype_mismatch"] += 1
                    if a.shape != b.shape:
                        S["shape_mismatch"] += 1; S["n_obj_byte_diff"] += 1; continue
                    S["n_elem_total"] += int(a.size)
                    if a.dtype.kind == "f":
                        na, nb = np.isnan(a), np.isnan(b)
                        S["n_nan_ref"] += int(na.sum()); S["n_nan_new"] += int(nb.sum())
                        if not np.array_equal(na, nb): S["nan_pattern_mismatch"] += 1
                    if bytes_equal(a, b):
                        continue
                    S["n_obj_byte_diff"] += 1
                    if a.dtype.kind == "f":
                        diff = ~((a == b) | (np.isnan(a) & np.isnan(b)))
                        # bit-pattern differences that compare equal (e.g. +0/-0, NaN payload)
                        bitdiff = a.view(np.uint32) != b.view(np.uint32)
                        S["n_bitdiff_but_value_equal"] = S.get("n_bitdiff_but_value_equal", 0) + int((bitdiff & ~diff).sum())
                        fin = diff & np.isfinite(a) & np.isfinite(b)
                        if fin.any():
                            ad = np.abs(a[fin].astype(np.float64) - b[fin].astype(np.float64))
                            den = np.maximum(np.abs(a[fin].astype(np.float64)), np.abs(b[fin].astype(np.float64)))
                            S["max_abs"] = max(S["max_abs"], float(ad.max()))
                            S["max_rel"] = max(S["max_rel"], float((ad / den).max()))
                            u = ulp_dist(a[fin], b[fin])
                            S["max_ulp"] = max(S["max_ulp"], int(u.max()))
                            for uu in np.minimum(u, 1000):
                                kk = str(int(uu)) if uu < 1000 else ">=1000"
                                S["ulp_hist"][kk] = S["ulp_hist"].get(kk, 0) + 1
                    else:
                        diff = a != b
                        S["max_abs"] = max(S["max_abs"], float(np.abs(a.astype(np.int64) - b.astype(np.int64)).max()))
                    S["n_elem_diff"] += int(diff.sum())
                    pix = np.where(diff.any(axis=0) if diff.ndim == 2 else diff)[0]
                    for r, (lo, hi) in REGIONS.items():
                        S["region_elem_diff"][r] += int(((wl[pix] >= lo) & (wl[pix] < hi)).sum())
                    if len(R["diff_examples"]) < 20:
                        R["diff_examples"].append({"class": c, "targetid": k[0], "program": k[1], "dset": d,
                                                   "n_diff": int(diff.sum()), "first_wl": float(wl[pix[0]])})
            R["obj_attrs"][c] = oa
    R["elapsed_s"] = time.time() - t0
    with open(out_fn, "w") as f:
        json.dump(R, f, indent=1, default=str)
    print(json.dumps({"counts": R["counts"], "datasets": R["datasets"]}, indent=1, default=str))

if __name__ == "__main__":
    main(*sys.argv[1:4])
