# ProcessDataQuality: Batch Record Validation & GMP Compliance

A validation framework for manufacturing batch record integrity, audit-trail
compliance, and automated QC checks, producing regulatory-ready compliance
reports.

> **Disclaimer:** This is a portfolio/demo project. All batch records,
> process parameters, audit-trail events, and deviations are **synthetically
> simulated** by `simulate_batch_records.py`. No real molecule, program, or
> employer data is used.

## What it does

1. **`simulate_batch_records.py`** simulates **160 biologics drug-substance
   batch records** (3 products) covering process parameters (bioreactor
   temp, pH, DO, agitation, culture duration), in-process control checks
   (VCD, viability, titer), release results (SEC purity, endotoxin,
   bioburden, host-cell protein), operator/QA sign-offs, and a full
   per-batch **audit trail** (who / when / what field / old value / new
   value) with ~2,400 logged events. A small set of integrity issues is
   deliberately injected into specific, known batch IDs:
   - 6 batches with a **missing operator sign-off**
   - 6 batches with an **out-of-sequence audit-trail timestamp** (an
     in-process-control event logged before the process parameters it
     depends on were recorded)
   - 6 batches whose **deviation record lacks a reviewer signature**

2. **`validate_batch_records.py`** is the validation framework. For every
   batch it checks:
   - **Completeness** - required fields and sign-offs present
   - **Audit-trail compliance** - a CREATE event exists, a RECORD event
     exists for every parameter the batch reports, a QA release SIGN event
     exists, and no event is timestamped earlier than an event it logically
     depends on
   - **Automated QC checks** - every process/IPC/release result checked
     against spec limits (`data/processed/specs.json`)
   - **Deviation documentation** - every deviation record must carry a
     reviewer signature

   It writes `data/processed/batch_compliance.csv` (one row per batch),
   `output/reports/deviation_log.csv` (one row per flagged issue), a
   regulatory-ready Markdown report per non-compliant batch
   (`output/reports/<batch_id>.md`), and a portfolio-level
   `output/reports/summary_report.md`.

3. **`run_all.py`** runs the whole pipeline.

## How to run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python run_all.py
pytest -q
```

## Results

_All numbers below are copied directly from an actual run of `python
run_all.py` - nothing is hardcoded. Fixed random seed makes them
reproducible._

- **160 batch records** simulated across 3 products, with **2,416 audit-trail
  events** and **12 deviation records**.
- **141 of 160 batches (88.1%) compliant**; **19 of 160 (11.9%)
  non-compliant**, each with a regulatory-ready report generated in
  `output/reports/`.
- Issue category breakdown across the 19 non-compliant batches (25 total
  flags, some batches have >1 issue):

  | Category | Count |
  |---|---|
  | AUDIT_SEQUENCE (out-of-sequence audit-trail timestamp) | 12 |
  | MISSING_SIGNOFF (missing operator sign-off) | 6 |
  | OOS_HIGH (result above spec upper limit) | 4 |
  | UNSIGNED_DEVIATION (deviation lacking reviewer signature) | 2 |
  | OOS_LOW (result below spec lower limit) | 1 |

- All **6 deliberately-injected missing-sign-off batches** and all **6
  injected out-of-sequence batches** were correctly flagged by the
  framework. Of the 6 injected unsigned-deviation batches, 2 happened to
  also receive a real deviation record in the simulation (the rest had no
  deviation at all that run, so there was nothing to leave unsigned) -
  both of those 2 were correctly flagged.
- The remaining 5 out-of-spec flags (OOS_HIGH/OOS_LOW) are **not injected**
  - they are natural statistical tail events from the simulated process
    distributions, caught by the automated QC spec checks exactly as a real
    QC system would catch them.
- `pytest -q` -> **8 passed**, including an explicit check that every
  injected integrity issue is caught by its corresponding validation check,
  and that clean batches are not falsely flagged.

## Project structure

```
simulate_batch_records.py   # batch record + audit trail simulation
validate_batch_records.py   # validation framework / QC checks / reports
run_all.py                  # runs the full pipeline
data/raw/                   # simulated batch records, audit trail, deviations
data/processed/             # specs, compliance table, injected-issue manifest
output/reports/             # per-batch + portfolio compliance reports
tests/test_pipeline.py      # pipeline + injected-issue sanity checks
```
