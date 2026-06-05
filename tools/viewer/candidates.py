"""
Load review candidates for the viewer's jump panel, from either:
  - float_candidates.json   (Stage-2 wedge-anchored complete floats)
  - rv_scan_results.json     (RV-morphology scan)

Both are normalized to a unified shape so the sidebar panel can jump to any of
them and drop the RV / RV->PA markers on the chart:
    {patient_id, segment_name, record_dir, jump_t, marker_t, transition_t, label}
"""

import json
import os

_DIR = os.path.dirname(os.path.dirname(__file__))
FLOAT_PATH = os.path.join(_DIR, "float_candidates.json")
RV_PATH = os.path.join(_DIR, "rv_scan_results.json")


def available_sources():
    """Return [(key, label), ...] for candidate files that exist on disk."""
    srcs = []
    if os.path.exists(FLOAT_PATH):
        srcs.append(("float", "Complete floats (wedge-anchored)"))
    if os.path.exists(RV_PATH):
        srcs.append(("rv", "RV-morphology scan"))
    return srcs


def load_candidates(source):
    """Load and normalize candidates for the given source key."""
    if source == "float":
        return _load_float()
    return _load_rv()


def _u(patient_id, segment_name, record_dir, jump_t, marker_t, transition_t, label):
    return {
        "patient_id": patient_id,
        "segment_name": segment_name,
        "record_dir": record_dir,
        "jump_t": jump_t,
        "marker_t": marker_t,
        "transition_t": transition_t,
        "label": label,
    }


def _load_float(min_score=4):
    if not os.path.exists(FLOAT_PATH):
        return []
    results = json.load(open(FLOAT_PATH)).get("results", [])
    results = [r for r in results if r.get("score", 0) >= min_score
               and r.get("rv_t") is not None]
    results.sort(key=lambda r: (r["score"], r.get("dia_stepup", 0)), reverse=True)
    out = []
    for r in results:
        stages = "".join([
            "R" if r.get("ra") else "-",
            "V" if r.get("rv") else "-",
            "P" if r.get("pa") else "-",
            "W" if r.get("wedge_after") else "-",
        ])
        label = (f"score {r['score']} [{stages}] step={r.get('dia_stepup', 0)}  "
                 f"{r['patient_id']} / {r['segment_name']}  "
                 f"RV@{r['rv_t']:.0f}s PA@{r['pa_t']:.0f}s")
        out.append(_u(r["patient_id"], r["segment_name"], r["record_dir"],
                      jump_t=r["rv_t"], marker_t=r["rv_t"],
                      transition_t=r.get("pa_t"), label=label))
    return out


def _load_rv():
    if not os.path.exists(RV_PATH):
        return []
    cands = json.load(open(RV_PATH)).get("candidates", [])
    for c in cands:
        c["_pid"] = c["record_dir"].rstrip("/").split("/")[-1]
    cands.sort(
        key=lambda c: (bool(c.get("transition")), c.get("longest_rv_run", 0),
                       c.get("best_rv", {}).get("score", 0.0)),
        reverse=True,
    )
    out = []
    for c in cands:
        best = c.get("best_rv", {})
        kind = "TRANS" if c.get("transition") else "RV"
        label = (f"[{kind}] run={c.get('longest_rv_run', '?')} "
                 f"s={best.get('score', 0):.2f}  {c['_pid']} / {c['segment_name']}  "
                 f"@{best.get('t_sec', 0):.0f}s")
        out.append(_u(c["_pid"], c["segment_name"], c["record_dir"],
                      jump_t=best.get("t_sec", 0.0),
                      marker_t=best.get("t_sec"),
                      transition_t=(c.get("transition_t")
                                    if c.get("transition") else None),
                      label=label))
    return out


def candidate_label(c, idx, total):
    """Compact one-line label for the candidate selectbox."""
    return f"{idx + 1:>2}/{total}  {c['label']}"
