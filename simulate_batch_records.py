"""
Simulates manufacturing batch records for a (fictional) biologics drug-substance
process: process parameters, in-process control (IPC) checks, release results,
operator sign-offs, and a per-batch audit-trail log (who / when / what-changed).

All data is synthetic. A small, deliberately-injected set of integrity issues
is seeded into specific batches so the validation framework in
`validate_batch_records.py` has real problems to catch:

  - missing sign-off      -> batch released without a QA sign-off event
  - out-of-sequence audit -> an audit-trail event timestamped before the
                              event that logically precedes it
  - unsigned deviation    -> a deviation record with no reviewer signature

Running this script with the same RANDOM_SEED always reproduces exactly the
same batches and exactly the same injected issues (their batch IDs are
written to data/processed/injected_issues.json for the test suite to check
against).
"""
from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

RANDOM_SEED = 42
N_BATCHES = 160  # "150+" per the spec

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "data" / "raw"
PROC_DIR = ROOT / "data" / "processed"
RAW_DIR.mkdir(parents=True, exist_ok=True)
PROC_DIR.mkdir(parents=True, exist_ok=True)

OPERATORS = [
    "J. Alvarez", "S. Chen", "M. Okafor", "R. Patel", "T. Nguyen",
    "L. Rossi", "K. Novak", "D. Kim", "A. Haddad", "E. Johansson",
]
QA_REVIEWERS = ["QA-1 M. Torres", "QA-2 B. Williams", "QA-3 P. Singh"]
SHIFTS = ["Day", "Swing", "Night"]
PRODUCTS = ["mAb-101 DS", "mAb-204 DS", "Fc-Fusion-07 DS"]

# Process parameter specs (mean/sd for simulation; limits used for QC checks)
PARAM_SPECS = {
    "bioreactor_temp_C": {"target": 37.0, "sd": 0.15, "low": 36.5, "high": 37.5},
    "ph": {"target": 7.0, "sd": 0.05, "low": 6.8, "high": 7.2},
    "dissolved_oxygen_pct": {"target": 40.0, "sd": 3.0, "low": 30.0, "high": 50.0},
    "agitation_rpm": {"target": 150.0, "sd": 4.0, "low": 140.0, "high": 160.0},
    "culture_duration_days": {"target": 14.0, "sd": 0.3, "low": 12.0, "high": 16.0},
}

# In-process control checks
IPC_SPECS = {
    "viable_cell_density_e6_per_mL": {"target": 8.0, "sd": 0.8, "low": 4.0, "high": 12.0},
    "viability_pct": {"target": 97.0, "sd": 1.2, "low": 90.0, "high": 100.0},
    "titer_g_per_L": {"target": 3.5, "sd": 0.35, "low": 2.0, "high": None},
}

# Release specs
RELEASE_SPECS = {
    "purity_pct_SEC": {"target": 98.5, "sd": 0.6, "low": 95.0, "high": None},
    "endotoxin_EU_per_mg": {"target": 0.05, "sd": 0.02, "low": None, "high": 0.5},
    "bioburden_CFU_per_mL": {"target": 0.0, "sd": 0.3, "low": None, "high": 1.0},
    "host_cell_protein_ng_per_mg": {"target": 50.0, "sd": 15.0, "low": None, "high": 100.0},
}

DEVIATION_CODES = [
    "DEV-TEMP-EXCURSION", "DEV-PH-DRIFT", "DEV-DO-LOW", "DEV-LATE-SAMPLE",
    "DEV-EQUIPMENT-ALARM", "DEV-OOS-RELEASE",
]


@dataclass
class AuditEvent:
    batch_id: str
    timestamp: str
    user: str
    action: str
    field: str
    old_value: str
    new_value: str


def clamp(v, low, high):
    if low is not None:
        v = max(v, low)
    if high is not None:
        v = min(v, high)
    return v


def gen_param_block(spec_dict, rng):
    out = {}
    for name, spec in spec_dict.items():
        val = rng.gauss(spec["target"], spec["sd"])
        out[name] = round(val, 3)
    return out


def main():
    rng = random.Random(RANDOM_SEED)

    batches = []
    audit_events: list[AuditEvent] = []
    deviations = []

    # Decide which batches get which injected issue (deterministic given seed)
    batch_ids = [f"BR-{2024000 + i}" for i in range(N_BATCHES)]
    issue_pool = rng.sample(batch_ids, k=18)  # 18 batches touched by issues
    missing_signoff_batches = set(issue_pool[0:6])
    out_of_sequence_batches = set(issue_pool[6:12])
    unsigned_deviation_batches = set(issue_pool[12:18])

    start_date = datetime(2024, 1, 5, 8, 0, 0)

    for i, batch_id in enumerate(batch_ids):
        product = rng.choice(PRODUCTS)
        operator = rng.choice(OPERATORS)
        shift = rng.choice(SHIFTS)
        qa_reviewer = rng.choice(QA_REVIEWERS)

        batch_start = start_date + timedelta(days=i * 1.7, hours=rng.uniform(-2, 2))
        t = batch_start

        # --- audit trail: batch opened ---
        t_open = t
        audit_events.append(AuditEvent(
            batch_id, t_open.isoformat(), operator, "CREATE", "batch_record",
            "", "opened"))

        process_params = gen_param_block(PARAM_SPECS, rng)
        t_params = t_open + timedelta(hours=1)
        for k, v in process_params.items():
            audit_events.append(AuditEvent(
                batch_id, t_params.isoformat(), operator, "RECORD", k, "", str(v)))

        ipc = gen_param_block(IPC_SPECS, rng)
        t_ipc = t_params + timedelta(hours=48)
        for k, v in ipc.items():
            audit_events.append(AuditEvent(
                batch_id, t_ipc.isoformat(), operator, "RECORD", k, "", str(v)))

        release = gen_param_block(RELEASE_SPECS, rng)
        t_release = t_ipc + timedelta(hours=72)
        for k, v in release.items():
            audit_events.append(AuditEvent(
                batch_id, t_release.isoformat(), rng.choice(OPERATORS), "RECORD", k, "", str(v)))

        # ~12% of batches get a real deviation
        has_deviation = rng.random() < 0.12
        deviation_record = None
        if has_deviation:
            code = rng.choice(DEVIATION_CODES)
            t_dev = t_release + timedelta(hours=2)
            dev_signed = batch_id not in unsigned_deviation_batches
            deviation_record = {
                "batch_id": batch_id,
                "deviation_code": code,
                "opened_by": operator,
                "opened_at": t_dev.isoformat(),
                "description": f"{code.replace('DEV-', '').replace('-', ' ').title()} observed during batch {batch_id}.",
                "reviewed_by": qa_reviewer if dev_signed else "",
                "reviewer_signature_present": dev_signed,
                "disposition": "Closed - No Impact" if dev_signed else "PENDING REVIEW",
            }
            deviations.append(deviation_record)
            audit_events.append(AuditEvent(
                batch_id, t_dev.isoformat(), operator, "CREATE", "deviation", "", code))
            if dev_signed:
                t_dev_sign = t_dev + timedelta(hours=6)
                audit_events.append(AuditEvent(
                    batch_id, t_dev_sign.isoformat(), qa_reviewer, "SIGN", "deviation_review", "", "signed"))

        # Operator sign-off
        t_signoff = t_release + timedelta(hours=4)
        has_signoff = batch_id not in missing_signoff_batches
        if has_signoff:
            audit_events.append(AuditEvent(
                batch_id, t_signoff.isoformat(), operator, "SIGN", "operator_signoff", "", "signed"))

        # QA release sign-off (requires operator signoff to exist upstream in real GMP;
        # here QA signs regardless so the "missing sign-off" issue is specifically operator-level)
        t_qa = t_signoff + timedelta(hours=8)
        audit_events.append(AuditEvent(
            batch_id, t_qa.isoformat(), qa_reviewer, "SIGN", "qa_release_signoff", "", "signed"))

        # Inject out-of-sequence audit timestamp: make the IPC record event
        # appear BEFORE the process-params event it logically follows.
        if batch_id in out_of_sequence_batches:
            # Find this batch's RECORD events for ipc keys and push one earlier than params
            for ev in audit_events:
                if ev.batch_id == batch_id and ev.field == "viable_cell_density_e6_per_mL":
                    bad_time = t_params - timedelta(hours=5)  # before params were even recorded
                    ev.timestamp = bad_time.isoformat()

        batch_row = {
            "batch_id": batch_id,
            "product": product,
            "operator": operator,
            "shift": shift,
            "qa_reviewer": qa_reviewer,
            "batch_start": t_open.isoformat(),
            "batch_release_eval": t_qa.isoformat(),
            **{f"param_{k}": v for k, v in process_params.items()},
            **{f"ipc_{k}": v for k, v in ipc.items()},
            **{f"release_{k}": v for k, v in release.items()},
            "has_deviation": has_deviation,
            "operator_signoff_present": has_signoff,
        }
        batches.append(batch_row)

    batches_df = pd.DataFrame(batches)
    audit_df = pd.DataFrame([asdict(e) for e in audit_events])
    deviations_df = pd.DataFrame(deviations)

    batches_df.to_csv(RAW_DIR / "batch_records.csv", index=False)
    audit_df.to_csv(RAW_DIR / "audit_trail.csv", index=False)
    deviations_df.to_csv(RAW_DIR / "deviations.csv", index=False)

    specs = {
        "process_params": PARAM_SPECS,
        "ipc": IPC_SPECS,
        "release": RELEASE_SPECS,
    }
    with open(PROC_DIR / "specs.json", "w") as f:
        json.dump(specs, f, indent=2)

    injected = {
        "missing_signoff_batches": sorted(missing_signoff_batches),
        "out_of_sequence_batches": sorted(out_of_sequence_batches),
        "unsigned_deviation_batches": sorted(unsigned_deviation_batches),
    }
    with open(PROC_DIR / "injected_issues.json", "w") as f:
        json.dump(injected, f, indent=2)

    print(f"Simulated {len(batches_df)} batch records -> {RAW_DIR / 'batch_records.csv'}")
    print(f"Audit trail: {len(audit_df)} events -> {RAW_DIR / 'audit_trail.csv'}")
    print(f"Deviations: {len(deviations_df)} records -> {RAW_DIR / 'deviations.csv'}")
    print(f"Injected issues: {injected}")


if __name__ == "__main__":
    main()
