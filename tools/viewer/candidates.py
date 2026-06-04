"""
Load RV-scan candidates (tools/rv_scan_results.json) so the viewer can jump
straight from a flagged candidate to its patient/segment/timestamp.
"""

import json
import os

RESULTS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "rv_scan_results.json"
)


def load_candidates():
    """Return the scan candidates, ranked best-first, each annotated with its
    patient_id. Empty list if the results file is missing.

    Ranking: transitions first, then longest sustained RV run, then RV score.
    """
    if not os.path.exists(RESULTS_PATH):
        return []
    with open(RESULTS_PATH) as f:
        data = json.load(f)
    cands = data.get("candidates", [])
    for c in cands:
        c["patient_id"] = c["record_dir"].rstrip("/").split("/")[-1]
    cands.sort(
        key=lambda c: (
            bool(c.get("transition")),
            c.get("longest_rv_run", 0),
            c.get("best_rv", {}).get("score", 0.0),
        ),
        reverse=True,
    )
    return cands


def candidate_label(c, idx, total):
    """Compact one-line label for the candidate selectbox."""
    kind = "TRANS" if c.get("transition") else "RV"
    best = c.get("best_rv", {})
    return (
        f"{idx + 1:>2}/{total}  {c['patient_id']} / {c['segment_name']}  "
        f"[{kind}] run={c.get('longest_rv_run', '?')} "
        f"s={best.get('score', 0):.2f} @{best.get('t_sec', 0):.0f}s"
    )
