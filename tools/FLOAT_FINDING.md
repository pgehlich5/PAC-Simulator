# Finding new PAC-float teaching patients (wedge-anchored pipeline)

This is the end-to-end guide for sourcing **new complete-float teaching cases** from the
MIMIC-III Waveform Database — recordings that capture a Swan-Ganz catheter advancing
**IVC/RA → RV → PA → wedge** on the PAP channel. It's how **Esther (p007452)** was found.

All waveform data is from `mimic3wdb-matched/1.0` (PhysioNet, **open access** — no
credentials needed for the waveforms themselves).

---

## Why this is hard

1. **The float is a brief transient.** Most PAP recordings show the catheter already parked
   in the PA for hours; the advancement lasts seconds-to-minutes and is usually not captured.
2. **RV and PA look alike in isolation.** They share nearly identical *systolic* pressure;
   only the *diastolic step-up* and the transition between them disambiguate. Classifying
   isolated windows as "RV vs PA" is fragile (a ventricularized PA or low-PADP patient mimics RV).

The fix to #2: **don't classify isolated windows — detect the ordered *sequence* of transitions.**
The fix to #1: **anchor the search on the rarest event (the wedge) and look backward.**

---

## The chamber signatures (the physiology the detectors encode)

| Chamber | Mean | Systolic | Diastolic | Pulse pressure | Morphology hallmark |
|---|---|---|---|---|---|
| IVC/RA | 0–8 | low | low | **narrow** | a/c/v venous waves |
| RV | ~12 | 20–30 | **~0–5** | **wide** | diastole *rises* (filling), no notch, often ectopy |
| PA | 10–20 | 20–30 | **8–15** | moderate | **dicrotic notch**, diastole *falls* (runoff) |
| Wedge | 6–12 | — | — | **collapses** | damped, a/v waves return |

The **three ordered transitions** are the real fingerprint:
1. **RA→RV:** systolic & pulse pressure *jump up* (into the ventricle).
2. **RV→PA:** diastolic *steps up* while systolic holds (across the pulmonic valve) — **the single
   most specific event.**
3. **PA→wedge:** pulse pressure *collapses*, mean drops (balloon wedges).

---

## The pipeline

```
catalog            Stage 1 (anchors)         Stage 2 (filter)          Review            Extract
pap_records.json → find_wedge.py          → find_float_leadin.py     → waveform_viewer  → extract_*.py
(2326 PAP segs)    wedge_candidates.json     float_candidates.json      candidate panel    waveform_data/<pt>/
```

### Catalog — `pap_records.json`
A list of all MIMIC-matched segments containing a PAP signal (built by `find_pap_records.py`,
header-only scan). Currently ~2,326 segments (and that catalog itself is only ~10% of the full
matched DB — lots of headroom). Each entry has `segment_name`, `record_dir`, `pap_channels`
(with per-segment `channel_index` — **do not hardcode a PAP channel; layouts vary**), `num_samples`.

### Stage 1 — `find_wedge.py`  →  `wedge_candidates.json`
Scans PAP segments for **wedge episodes** (transient pulse-pressure collapse bounded by PA,
scored by pulsatility ratio + cardiac-regularity autocorrelation). Anchoring on the wedge is
efficient because wedges are rare. Fetches **PAP-only** (`channels=[idx]`). Sorts shortest-first,
resumable.

```bash
python find_wedge.py --max-segments 500 --min-duration 5 --resume
```
> A wedge with a **routine** lead-in (steady PA, no RV) is a normal periodic balloon inflation —
> Stage 2 discards those. A wedge preceded by a fresh RV→RA sequence is the rare **insertion float**.

### Stage 2 — `find_float_leadin.py`  →  `float_candidates.json`
For each wedge anchor, fetches the PAP **around** it and searches the whole span for the ordered
**RA(lowPP) → RV → PA(step-up) → wedge(lowPP)** sequence. RA and wedge are both low-pulse-pressure
and are disambiguated **purely by position** (RA before the RV, wedge after the PA). Reuses the
RV-morphology detector + physiologic gates from `rv_morphology_probe.py`.

```bash
python find_float_leadin.py            # reads wedge_candidates.json, writes float_candidates.json
```

**Score 0–6:** `+2` RV→PA core, `+2` diastolic step-up (≥5 mmHg), `+1` RA inflow before RV,
`+1` wedge after PA. **Score 6 = complete float.** The stage string is `[RVPW]` (R=RA, V=RV,
P=PA, W=wedge-after; `-` = stage missing).

### Review — the viewer's "Review candidates" panel
`python -m streamlit run waveform_viewer.py`, then set **Source → "Complete floats (wedge-anchored)."**
Selecting a candidate auto-jumps to its patient/segment and drops the **RV flag** (orange) and
**RV→PA transition** (cyan) markers on the chart. Eyeball each — the detector is a *filter*, not a
verdict; a physician confirms.

### Extract — `extract_p003914.py` (Grover) / `extract_p007452.py` (Esther) as templates
Once a float is confirmed, pin the chamber time-boundaries by scrubbing, then write a
per-patient extract script. It produces `waveform_data/<pt>/` with:
`background/` (II + ABP, a clean window — used continuously), `pap_svc|ra|rv|pa|wedge/`
(per-chamber PAP clips), and `patient.json`. Conventions used for Esther:
- **SVC and RA can share one clip** (they look identical).
- **`background_rv/` is optional** — only add it (longer ~20–60s ECG+ABP window) if the RV passage
  shows real ectopy; a 7s RV is too short to loop as a background.
- **Trim stray beats / loop points.** Raw cuts can start/end mid-beat or include a contaminating
  beat from the adjacent chamber (e.g. Esther's wedge had a stray RV beat at the end — trimmed to
  328.6s). Check per-second min/max to find the offender.
- If the ECG lead isn't `II` (e.g. `MCL1`), save it under the `II` slot so the background loader
  picks it up. The simulator background expects signals `II` + `ABP`.

---

## Validation (always do this first)

Both Grover (p003914) and Esther (p007452) are known complete floats in one contiguous segment,
so they're ground truth. Stage 2 on Grover's wedge reconstructs **RV@116s, PA@232s, step-up 15.2,
RA before, wedge after → score 6**. If a change breaks Grover, it's wrong.

---

## Known failure modes / caveats

- **Negative RV→PA step-up = NOT a real float.** If "PA" diastolic is *lower* than "RV", the
  morphology is backwards — usually CPR/disorganized rhythm or artifact faking the shape
  (e.g. p007612, a captured code, scraped score 4 with step-up −2.5). Good auto-reject signal.
- **Corrupted / railing signals** (railed at a constant, negative diastolic) are rejected by
  `physiologic_ok()` in `rv_morphology_probe.py` (longest-flat-run + range + missing-data checks)
  before scoring. This is why the *second* DB scan stopped surfacing the garbage the first one did.
- **RA vs wedge** are both narrow-PP; only **sequence position** tells them apart.
- **The scan covers a tiny slice** so far (~2% of recording time, shortest-segments-first; catalog
  is ~10% of the matched DB). Scanning the long segments and extending the catalog = much headroom.
- The scanner surfaces dramatic **non-floats** worth a look but not for teaching (deaths, codes) —
  see `## Notable Database Findings` in CLAUDE.md (p007532 comfort-care death, p007612 code).

---

## Tuning knobs

- `rv_morphology_probe.py`: `MIN_RV_PULSE_PRESSURE` (RV vs atrial gate), `MIN_PLAUSIBLE_DIASTOLIC`,
  `MAX_FLAT_FRACTION_OF_FS` (clip/rail detector), the nadir/slope scoring in `rv_likeness`.
- `find_float_leadin.py`: `WEDGE_PP_MAX` (lowPP threshold), `DIA_STEPUP_MIN`, `RV_RUN_MIN`/`PA_RUN_MIN`,
  `LOOKBACK_MIN`/`FORWARD_MIN` (fetch span around the wedge).
- `find_wedge.py`: `MAX_WEDGE_PULSE_PRESSURE`, `PULSATILITY_RATIO_THRESH`, wedge mean/duration bounds.

Generated outputs (`wedge_candidates.json`, `float_candidates.json`, `rv_scan_results.json`,
`*_progress.json`) are **regenerable and gitignored/untracked** — rerun the stages to rebuild them.
