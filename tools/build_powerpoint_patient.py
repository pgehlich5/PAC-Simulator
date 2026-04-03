"""Build the powerpoint patient's waveform files from digitized CSVs."""
import json
import os
import shutil
import pandas as pd

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
PATIENT_DIR = os.path.join(TOOLS_DIR, "..", "waveform_data", "powerpoint")

# Map: (chamber_folder, source_csv, source_column)
CHAMBERS = {
    "pap_svc": ("wpd_ra_resampled.csv", "RAP"),    # SVC uses RA waveform
    "pap_ra":  ("wpd_ra_resampled.csv", "RAP"),
    "pap_rv":  ("wpd_rv_resampled.csv", None),      # column is "PAP" (legacy name)
    "pap_pa":  ("wpd_pa_resampled.csv", "PAP"),
    "pap_wedge": ("wpd_wedge_resampled.csv", "PAWP"),
}

for chamber, (src_csv, src_col) in CHAMBERS.items():
    src_path = os.path.join(TOOLS_DIR, src_csv)
    df = pd.read_csv(src_path)

    # Get the data column (first column if src_col is None or not found)
    if src_col and src_col in df.columns:
        data = df[src_col].values
    else:
        data = df.iloc[:, 0].values

    num_samples = len(data)

    # Write PAP.csv in the expected format
    dest_dir = os.path.join(PATIENT_DIR, chamber)
    csv_path = os.path.join(dest_dir, "PAP.csv")
    with open(csv_path, "w") as f:
        f.write("PAP (mmHg)\n")
        for val in data:
            f.write(f"{val:.4f}\n")

    # Write metadata.json
    meta = {
        "case_name": chamber,
        "description": f"PAP waveform for {chamber} - Digitized from UCHealth PowerPoint slide",
        "source": {
            "database": "WebPlotDigitizer",
            "origin": "UCHealth PA Catheter teaching slide"
        },
        "sampling_rate_hz": 125,
        "num_samples": num_samples,
        "signals": [
            {
                "signal_name": "PAP",
                "original_label": "PAP",
                "units": "mmHg",
                "num_samples": num_samples,
                "file": "PAP.csv"
            }
        ]
    }
    meta_path = os.path.join(dest_dir, "metadata.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    print(f"{chamber}: {num_samples} samples ({num_samples/125:.1f}s) -> {csv_path}")

print("\nDone! Patient files created.")
