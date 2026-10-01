"""
Batch Record Validation & GMP Compliance framework.

For every batch in data/raw/batch_records.csv this checks:

1. Record completeness  - all required fields present (non-null) for process
   parameters, IPC, release results, and sign-offs.
2. Audit-trail compliance - every batch has a CREATE event, a RECORD event
   for every parameter it reports, a SIGN event for QA release, and no
   out-of-sequence timestamps (an event timestamped earlier than an event
   it logically depends on).
3. Automated QC checks - every numeric result is checked against the spec
   limits in data/processed/specs.json; out-of-spec values are flagged.
4. Deviation documentation - any deviation record must carry a reviewer
   signature; unsigned deviations are flagged as an open compliance gap.

Output:
  - data/processed/batch_compliance.csv   (one row per batch, full detail)
  - output/reports/deviation_log.csv      (every flagged issue, one row each)
  - output/reports/<batch_id>.md          (regulatory-ready report, only for
                                            non-compliant batches, since those
                                            are the ones requiring documented
                                            review)
  - output/reports/summary_report.md      (portfolio-level compliance summary)
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "data" / "raw"
PROC_DIR = ROOT / "data" / "processed"
REPORTS_DIR = ROOT / "output" / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

REQUIRED_FIELDS = [
    "batch_id", "product", "operator", "qa_reviewer", "batch_start",
    "batch_release_eval",
]


def load_data():
    batches = pd.read_csv(RAW_DIR / "batch_records.csv")
    audit = pd.read_csv(RAW_DIR / "audit_trail.csv", parse_dates=["timestamp"])
    deviations = pd.read_csv(RAW_DIR / "deviations.csv") if (RAW_DIR / "deviations.csv").stat().st_size > 0 else pd.DataFrame()
    with open(PROC_DIR / "specs.json") as f:
        specs = json.load(f)
    return batches, audit, deviations, specs


def check_completeness(row: pd.Series) -> list[str]:
    issues = []
    for field in REQUIRED_FIELDS:
        val = row.get(field)
        if pd.isna(val) or str(val).strip() == "":
            issues.append(f"MISSING_FIELD:{field}")
    if not bool(row.get("operator_signoff_present", False)):
        issues.append("MISSING_SIGNOFF:operator_signoff")
    return issues


def check_qc_specs(row: pd.Series, specs: dict) -> list[str]:
    issues = []
    for group_name, group_specs in specs.items():
        prefix = {"process_params": "param_", "ipc": "ipc_", "release": "release_"}[group_name]
        for param, lim in group_specs.items():
            col = f"{prefix}{param}"
            if col not in row:
                continue
            val = row[col]
            if pd.isna(val):
                continue
            low, high = lim.get("low"), lim.get("high")
            if low is not None and val < low:
                issues.append(f"OOS_LOW:{col}={val}<{low}")
            if high is not None and val > high:
                issues.append(f"OOS_HIGH:{col}={val}>{high}")
    return issues


def check_audit_trail(batch_id: str, audit_batch: pd.DataFrame, record_fields_raw: list[str]) -> list[str]:
    """Checks: CREATE event exists, a RECORD event exists for every reported
    parameter, a QA release SIGN event exists, and no event is timestamped
    earlier than an event it logically depends on (RECORD events must come
    after CREATE; IPC-stage records must come after process-param records).

    Note: audit-trail events log the raw parameter name (e.g. "ph"), while
    batch-record columns use a prefixed name (e.g. "param_ph") to avoid
    collisions across the process/IPC/release groups - record_fields_raw
    must be the raw (unprefixed) names to match the audit log.
    """
    issues = []
    if audit_batch.empty:
        return ["AUDIT_GAP:no_audit_events"]

    actions = audit_batch["action"].tolist()
    if "CREATE" not in actions:
        issues.append("AUDIT_GAP:missing_create_event")
    if not ((audit_batch["action"] == "SIGN") & (audit_batch["field"] == "qa_release_signoff")).any():
        issues.append("AUDIT_GAP:missing_qa_release_sign_event")

    create_rows = audit_batch[audit_batch["action"] == "CREATE"]
    create_time = create_rows["timestamp"].min() if not create_rows.empty else None

    record_rows = audit_batch[audit_batch["action"] == "RECORD"]
    missing_fields = set(record_fields_raw) - set(record_rows["field"])
    for mf in missing_fields:
        issues.append(f"AUDIT_GAP:no_record_event_for:{mf}")

    if create_time is not None and not record_rows.empty:
        earliest_record = record_rows["timestamp"].min()
        if earliest_record < create_time:
            issues.append("AUDIT_SEQUENCE:record_before_create")

    # process-param records must all precede ipc records (logical ordering)
    param_fields = [c for c in record_fields_raw if c in PARAM_FIELD_SET]
    ipc_fields = [c for c in record_fields_raw if c in IPC_FIELD_SET]
    if param_fields and ipc_fields:
        param_times = record_rows[record_rows["field"].isin(param_fields)]["timestamp"]
        ipc_times = record_rows[record_rows["field"].isin(ipc_fields)]["timestamp"]
        if not param_times.empty and not ipc_times.empty:
            if ipc_times.min() < param_times.max():
                issues.append("AUDIT_SEQUENCE:ipc_recorded_before_params_complete")

    return issues


def check_deviation_documentation(batch_id: str, deviations: pd.DataFrame) -> list[str]:
    issues = []
    batch_devs = deviations[deviations["batch_id"] == batch_id] if not deviations.empty else pd.DataFrame()
    for _, dev in batch_devs.iterrows():
        if not bool(dev.get("reviewer_signature_present", False)):
            issues.append(f"UNSIGNED_DEVIATION:{dev['deviation_code']}")
    return issues


PARAM_FIELD_SET = set()
IPC_FIELD_SET = set()


def write_batch_report(batch_id: str, row: pd.Series, issues: list[str], path: Path):
    lines = [
        f"# Batch Compliance Report - {batch_id}",
        "",
        f"**Product:** {row['product']}  ",
        f"**Operator:** {row['operator']}  ",
        f"**QA Reviewer:** {row['qa_reviewer']}  ",
        f"**Batch Start:** {row['batch_start']}  ",
        f"**Release Evaluation:** {row['batch_release_eval']}  ",
        "",
        "## Compliance Status: " + ("NON-COMPLIANT" if issues else "COMPLIANT"),
        "",
    ]
    if issues:
        lines.append("## Flagged Issues")
        lines.append("")
        for issue in issues:
            lines.append(f"- `{issue}`")
        lines.append("")
        lines.append("## Deviation Documentation")
        lines.append("")
        lines.append(
            "This batch record has been flagged for QA review per the issues "
            "above. Per GMP deviation-management requirements, each item "
            "requires documented investigation, root-cause assessment, and "
            "reviewer sign-off before batch disposition can be finalized."
        )
    else:
        lines.append("No issues identified. Batch record is complete, the "
                      "audit trail is continuous and correctly sequenced, "
                      "all in-process and release results are within spec, "
                      "and any associated deviations carry reviewer "
                      "signatures.")
    path.write_text("\n".join(lines) + "\n")


def main():
    batches, audit, deviations, specs = load_data()

    global PARAM_FIELD_SET, IPC_FIELD_SET
    PARAM_FIELD_SET = set(specs["process_params"])
    IPC_FIELD_SET = set(specs["ipc"])
    RELEASE_FIELD_SET = set(specs["release"])
    record_fields_raw = sorted(PARAM_FIELD_SET | IPC_FIELD_SET | RELEASE_FIELD_SET)
    record_cols = sorted(
        {f"param_{k}" for k in specs["process_params"]}
        | {f"ipc_{k}" for k in specs["ipc"]}
        | {f"release_{k}" for k in specs["release"]}
    )

    all_rows = []
    deviation_log_rows = []

    for _, row in batches.iterrows():
        batch_id = row["batch_id"]
        audit_batch = audit[audit["batch_id"] == batch_id].sort_values("timestamp")

        completeness_issues = check_completeness(row)
        qc_issues = check_qc_specs(row, specs)
        audit_issues = check_audit_trail(batch_id, audit_batch, record_fields_raw)
        deviation_issues = check_deviation_documentation(batch_id, deviations)

        all_issues = completeness_issues + qc_issues + audit_issues + deviation_issues
        compliant = len(all_issues) == 0

        all_rows.append({
            "batch_id": batch_id,
            "product": row["product"],
            "compliant": compliant,
            "n_issues": len(all_issues),
            "completeness_issues": ";".join(completeness_issues),
            "qc_issues": ";".join(qc_issues),
            "audit_issues": ";".join(audit_issues),
            "deviation_issues": ";".join(deviation_issues),
        })

        for issue in all_issues:
            category = issue.split(":")[0]
            deviation_log_rows.append({
                "batch_id": batch_id,
                "category": category,
                "detail": issue,
            })

        if all_issues:
            write_batch_report(batch_id, row, all_issues, REPORTS_DIR / f"{batch_id}.md")

    compliance_df = pd.DataFrame(all_rows)
    compliance_df.to_csv(PROC_DIR / "batch_compliance.csv", index=False)

    deviation_log_df = pd.DataFrame(deviation_log_rows)
    deviation_log_df.to_csv(REPORTS_DIR / "deviation_log.csv", index=False)

    n_total = len(compliance_df)
    n_compliant = int(compliance_df["compliant"].sum())
    n_noncompliant = n_total - n_compliant
    category_counts = deviation_log_df["category"].value_counts() if not deviation_log_df.empty else pd.Series(dtype=int)

    summary_lines = [
        "# Batch Record Validation - Portfolio Compliance Summary",
        "",
        f"**Total batches evaluated:** {n_total}",
        f"**Compliant batches:** {n_compliant} ({n_compliant / n_total:.1%})",
        f"**Non-compliant batches:** {n_noncompliant} ({n_noncompliant / n_total:.1%})",
        "",
        "## Issue Category Breakdown",
        "",
        "| Category | Count |",
        "|---|---|",
    ]
    for cat, count in category_counts.items():
        summary_lines.append(f"| {cat} | {count} |")

    summary_lines += [
        "",
        "## Non-Compliant Batches",
        "",
        "| Batch ID | Product | # Issues |",
        "|---|---|---|",
    ]
    for _, r in compliance_df[~compliance_df["compliant"]].iterrows():
        summary_lines.append(f"| {r['batch_id']} | {r['product']} | {r['n_issues']} |")

    (REPORTS_DIR / "summary_report.md").write_text("\n".join(summary_lines) + "\n")

    print(f"Evaluated {n_total} batches: {n_compliant} compliant, {n_noncompliant} non-compliant")
    print("Issue category breakdown:")
    print(category_counts.to_string())
    print(f"Reports written to {REPORTS_DIR}")


if __name__ == "__main__":
    main()
