import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module", autouse=True)
def run_pipeline():
    """Runs the real pipeline once for the whole test module."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "run_all.py")],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"run_all.py failed:\n{result.stdout}\n{result.stderr}"
    yield


def test_150_plus_batches_present():
    batches = pd.read_csv(ROOT / "data" / "raw" / "batch_records.csv")
    assert len(batches) >= 150
    assert batches["batch_id"].is_unique


def test_audit_trail_present_for_every_batch():
    batches = pd.read_csv(ROOT / "data" / "raw" / "batch_records.csv")
    audit = pd.read_csv(ROOT / "data" / "raw" / "audit_trail.csv")
    batch_ids = set(batches["batch_id"])
    audited_ids = set(audit["batch_id"])
    assert batch_ids <= audited_ids


def test_injected_missing_signoffs_are_caught():
    with open(ROOT / "data" / "processed" / "injected_issues.json") as f:
        injected = json.load(f)
    compliance = pd.read_csv(ROOT / "data" / "processed" / "batch_compliance.csv")

    for batch_id in injected["missing_signoff_batches"]:
        row = compliance[compliance["batch_id"] == batch_id].iloc[0]
        assert not row["compliant"]
        assert "MISSING_SIGNOFF" in row["completeness_issues"]


def test_injected_out_of_sequence_audit_events_are_caught():
    with open(ROOT / "data" / "processed" / "injected_issues.json") as f:
        injected = json.load(f)
    compliance = pd.read_csv(ROOT / "data" / "processed" / "batch_compliance.csv")

    for batch_id in injected["out_of_sequence_batches"]:
        row = compliance[compliance["batch_id"] == batch_id].iloc[0]
        assert not row["compliant"]
        assert "AUDIT_SEQUENCE" in row["audit_issues"]


def test_injected_unsigned_deviations_are_caught():
    with open(ROOT / "data" / "processed" / "injected_issues.json") as f:
        injected = json.load(f)
    compliance = pd.read_csv(ROOT / "data" / "processed" / "batch_compliance.csv")
    deviations = pd.read_csv(ROOT / "data" / "raw" / "deviations.csv")

    checked_any = False
    for batch_id in injected["unsigned_deviation_batches"]:
        batch_devs = deviations[deviations["batch_id"] == batch_id]
        if batch_devs.empty:
            # Only batches that actually have a deviation can show this issue
            continue
        checked_any = True
        row = compliance[compliance["batch_id"] == batch_id].iloc[0]
        assert not row["compliant"]
        assert "UNSIGNED_DEVIATION" in row["deviation_issues"]
    assert checked_any, "No unsigned-deviation batch actually had a deviation record to check"


def test_clean_batches_are_not_falsely_flagged():
    with open(ROOT / "data" / "processed" / "injected_issues.json") as f:
        injected = json.load(f)
    tainted = set(injected["missing_signoff_batches"]) | set(injected["out_of_sequence_batches"]) | set(injected["unsigned_deviation_batches"])
    compliance = pd.read_csv(ROOT / "data" / "processed" / "batch_compliance.csv")
    deviations = pd.read_csv(ROOT / "data" / "raw" / "deviations.csv")

    # A clean batch (no injected issue) with no deviation at all should be compliant
    clean = compliance[~compliance["batch_id"].isin(tainted)]
    clean_no_dev = clean[~clean["batch_id"].isin(deviations["batch_id"])]
    assert clean_no_dev["compliant"].mean() > 0.9  # allow for rare natural OOS values


def test_reports_generated():
    reports_dir = ROOT / "output" / "reports"
    assert (reports_dir / "summary_report.md").exists()
    assert (reports_dir / "deviation_log.csv").exists()

    compliance = pd.read_csv(ROOT / "data" / "processed" / "batch_compliance.csv")
    non_compliant_ids = compliance[~compliance["compliant"]]["batch_id"].tolist()
    assert len(non_compliant_ids) > 0
    for batch_id in non_compliant_ids[:5]:
        assert (reports_dir / f"{batch_id}.md").exists()


def test_deviation_log_not_empty_when_issues_exist():
    deviation_log = pd.read_csv(ROOT / "output" / "reports" / "deviation_log.csv")
    assert len(deviation_log) > 0
    assert set(["batch_id", "category", "detail"]) <= set(deviation_log.columns)
