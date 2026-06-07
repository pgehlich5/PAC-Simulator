"""
Extract waveform data for patient p007251 ("Horace") from MIMIC-III segment
3259702_0173 -- a complete PAC float with SEVERE pulmonary hypertension
(PAP ~74/44) and subtle ectopy throughout.

Chamber time ranges (physician-provided):
  SVC/RA: 0 - 28.45s   (shared clip)
  RV:     28.45 - 63s
  PA:     242.8 - 325s
  Wedge:  368 - 393s

PER-CHAMBER backgrounds: unlike Esther (one shared background), Horace captures
ECG(II)+ABP cut from EACH chamber's own time window, so the subtle ectopy stays
time-locked with that chamber's PAP. The simulator swaps the background per
chamber (generalized background_rv mechanism) and resets it in sync with the PAP.

Folders produced:
  background/            II+ABP (= SVC/RA window) - default + drives display_signals
  background_svc/ _ra/ _rv/ _pa/ _wedge/   per-chamber II+ABP (synced ectopy)
  pap_svc/ _ra/ _rv/ _pa/ _wedge/          per-chamber PAP
"""

import os
import json
import numpy as np
import wfdb

SEGMENT = "3259702_0173"
RECORD_DIR = "mimic3wdb-matched/1.0/p00/p007251"
FS = 125
OUTPUT_BASE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "waveform_data", "p007251",
)

# Unique time windows (seconds)
WIN = {
    "svc_ra": (0.0, 28.45),
    "rv":     (28.45, 63.0),
    "pa":     (242.8, 325.0),
    "wedge":  (368.0, 393.0),
}

# PAP chamber folder -> window key
PAP_FOLDERS = {
    "pap_svc": "svc_ra", "pap_ra": "svc_ra", "pap_rv": "rv",
    "pap_pa": "pa", "pap_wedge": "wedge",
}
# Background folder -> window key ("background" is the default + display driver)
BG_FOLDERS = {
    "background": "svc_ra",
    "background_svc": "svc_ra", "background_ra": "svc_ra",
    "background_rv": "rv", "background_pa": "pa", "background_wedge": "wedge",
}


def fetch(start_sec, end_sec):
    print(f"  fetch {start_sec}-{end_sec}s")
    return wfdb.rdrecord(SEGMENT, pn_dir=RECORD_DIR,
                         sampfrom=int(start_sec * FS), sampto=int(end_sec * FS))


def sig(rec, name):
    return rec.p_signal[:, rec.sig_name.index(name)]


def save_csv(folder, name, data, units):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, f"{name}.csv"), "w") as f:
        f.write(f"{name} ({units})\n")
        for v in data:
            f.write("0.0\n" if np.isnan(v) else f"{v:.4f}\n")


def save_meta(folder, case_name, desc, start_sec, dur_sec, signals_info):
    meta = {
        "case_name": case_name, "description": desc,
        "source": {"database": "mimic3wdb-matched/1.0", "patient_id": "p007251",
                   "segment_name": SEGMENT, "record_dir": RECORD_DIR,
                   "start_seconds": start_sec, "duration_seconds": dur_sec},
        "sampling_rate_hz": FS, "num_samples": signals_info[0]["num_samples"],
        "signals": signals_info,
    }
    with open(os.path.join(folder, "metadata.json"), "w") as f:
        json.dump(meta, f, indent=2)


def main():
    print(f"Extracting p007251 (Horace) from {SEGMENT}\n")
    # Fetch each unique window once (all channels), keep II/ABP/PAP
    win_data = {}
    for key, (a, b) in WIN.items():
        rec = fetch(a, b)
        win_data[key] = {"II": sig(rec, "II"), "ABP": sig(rec, "ABP"),
                         "PAP": sig(rec, "PAP"), "start": a, "dur": b - a}

    # PAP chamber clips
    for folder, wk in PAP_FOLDERS.items():
        d = win_data[wk]
        save_csv(os.path.join(OUTPUT_BASE, folder), "PAP", d["PAP"], "mmHg")
        save_meta(os.path.join(OUTPUT_BASE, folder), folder,
                  f"PAP for {folder} - Patient p007251", d["start"], d["dur"],
                  [{"signal_name": "PAP", "original_label": "PAP", "units": "mmHg",
                    "num_samples": len(d["PAP"]), "file": "PAP.csv"}])
        print(f"  wrote {folder} ({len(d['PAP'])} samples)")

    # Background clips (II + ABP), default + per-chamber (synced ectopy)
    for folder, wk in BG_FOLDERS.items():
        d = win_data[wk]
        path = os.path.join(OUTPUT_BASE, folder)
        save_csv(path, "II", d["II"], "mV")
        save_csv(path, "ABP", d["ABP"], "mmHg")
        save_meta(path, folder,
                  f"ECG(II)+ABP for {folder} - Patient p007251", d["start"], d["dur"],
                  [{"signal_name": "II", "original_label": "II", "units": "mV",
                    "num_samples": len(d["II"]), "file": "II.csv"},
                   {"signal_name": "ABP", "original_label": "ABP", "units": "mmHg",
                    "num_samples": len(d["ABP"]), "file": "ABP.csv"}])
        print(f"  wrote {folder} ({len(d['II'])} samples)")

    patient_json = {
        "nickname": "Horace",
        "subject_id": 7251,
        "mimic_id": "p007251",
        "age": None, "sex": None,
        "summary": "Complete PAC float with SEVERE pulmonary hypertension "
                   "(PAP ~74/44). Catheter advanced SVC/RA -> RV -> PA -> wedge. "
                   "Found by the wedge-anchored scan. Clinical details TBD.",
        "notes": "Per-chamber ECG+ABP backgrounds capture subtle ectopy time-locked "
                 "to each chamber's PAP (generalized background swap).",
    }
    with open(os.path.join(OUTPUT_BASE, "patient.json"), "w") as f:
        json.dump(patient_json, f, indent=4)
    print(f"\n=== DONE === {OUTPUT_BASE}")


if __name__ == "__main__":
    main()
