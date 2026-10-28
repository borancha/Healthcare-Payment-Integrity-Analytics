# AI-Powered Healthcare Payment Integrity & Provider Anomaly Analytics

## Project Overview

This project analyzes publicly available CMS Medicare Physician & Other Practitioners data to identify unusual provider/service payment patterns for human review.

It demonstrates a reproducible analytics workflow using Python, DuckDB, statistical peer benchmarks, an IQR-based outlier scoring pipeline, automated tests, and a Streamlit dashboard.

**Important:** A statistical outlier is a review candidate, not proof of fraud, provider misconduct, or an improper payment.

## Data Source

- CMS Medicare Physician & Other Practitioners — by Provider and Service
- Official dataset: https://data.cms.gov/provider-summary-by-type-of-service/medicare-physician-other-practitioners
- Data dictionary: https://data.cms.gov/resources/medicare-physician-other-practitioners-by-provider-and-service-data-dictionary
- Reporting year: 2024

The source contains aggregated provider/service records, not individual adjudicated claims. Raw and generated datasets are excluded from this repository; users must obtain the data from CMS separately.

## Methodology

1. Profile and validate source data.
2. Construct payment reference benchmarks by provider type, HCPCS code, and place of service.
3. Use a provider-level split to separate reference and evaluation populations.
4. Apply IQR-based bounds to flag unusually high or low average Medicare payments.
5. Prioritize flagged records for human review using transparent distance and peer-support rules.
6. Validate scoring reproducibility and classification logic.
7. Present review candidates and supporting peer statistics in a Streamlit dashboard.

## Technology Stack

- Python, pandas, NumPy
- DuckDB
- scikit-learn
- Streamlit
- Plotly
- unittest
- Git

## Repository Structure

- `src/` — reusable scoring pipeline
- `dashboard/` — Streamlit dashboard
- `tests/` — automated classification tests
- `documentation/` — methodology and data-quality documentation
- `data/` — local datasets, excluded from Git

## Run Locally

### 1. Clone the repository

```bash
git clone https://github.com/borancha/healthcare-payment-integrity-analytics.git
cd healthcare-payment-integrity-analytics
```

### 2. Install dependencies

Make sure Python and pip are installed, then run:

```bash
python -m pip install -r requirements.txt
```
### 3. Prepare the local data and analytics artifacts

The CMS source data and generated analytics artifacts are excluded from Git because of their size. Download the 2024 Medicare Physician & Other Practitioners dataset from the official [CMS dataset page](https://data.cms.gov/provider-summary-by-type-of-service/medicare-physician-other-practitioners) and prepare the processed CSV at:

`data/processed/cms_medicare_provider_service_2024.csv`

**Data preparation prerequisite:** Prepare the processed CSV separately from the official CMS download; this repository does not currently include an automated raw-to-processed conversion script. The benchmark builder validates required columns before processing. Python 3.12.15 was used for local validation.

From the repository root, build the reference benchmarks and held-out evaluation dataset:

```bash
python src/build_reference_benchmarks.py
python src/create_evaluation_dataset.py
python src/score_provider_payments.py --input data/analytics/rebuild_v1/evaluation_input_2024.parquet --benchmarks data/analytics/rebuild_v1/reference_payment_benchmarks_2024.parquet --output data/analytics/rebuild_v1/evaluation_scored_2024.parquet
python src/summarize_evaluation.py
```

Generate the machine-learning screening evidence used by the dashboard:

```bash
python src/score_payment_review_candidates.py
```

These commands create local Parquet and JSON artifacts under `data/analytics/rebuild_v1/` and `data/analytics/payment_review_ml_v1/`. The scripts protect existing output files. If an output already exists, inspect it before choosing a new output directory or path.

**Before launching the dashboard, confirm that these files exist:**

- `data/analytics/rebuild_v1/evaluation_scored_2024.parquet`
- `data/analytics/payment_review_ml_v1/payment_review_ml_scores.parquet`

The dashboard supports optional LLM-generated explanations through a local Ollama service using the `qwen2.5:3b` model. This feature requires Ollama to be installed and running, with the model available. Ollama is not required to generate the core benchmark or machine-learning scores.
### 4. Launch the dashboard

From the project root, run:

```bash
python -m streamlit run dashboard/app.py
```

### 5. Run the automated tests

From the project root, run:

```bash
python -m pytest -v
```
**Note:** The GitHub repository contains the code and documentation, not the large local datasets or generated analytics artifacts. The dashboard may not launch successfully until those files have been prepared.
## Interpretation and Limitations

- Average Medicare payment is not total paid amount.
- Statistical outliers require contextual review and are not confirmed improper payments.
- Peer benchmarks depend on provider type, HCPCS code, place of service, and reference sample size.
- Historical 2024 benchmarks should not be assumed valid for future periods without monitoring and recalibration.
- This project does not establish fraud probability, provider misconduct, financial recovery, or clinical appropriateness.

## Validation

The project includes unit tests for classification rules and documentation of scoring methodology and evaluation procedures. Validation of scoring logic does not establish the real-world accuracy of improper-payment detection.

### Reproducibility Validation

The scoring pipeline was independently rerun against a held-out provider-service dataset containing **1,946,987 records**.

- **Original records:** 1,946,987
- **Reproduced records:** 1,946,987
- **Rows present only in the original output:** 0
- **Rows present only in the reproduced output:** 0
- **Result:** PASS — exact output match.

The comparison was performed using `compare_scoring_outputs.py`, which checks both outputs in both directions, including duplicate rows. The result demonstrates reproducibility for the tested input and benchmark artifacts. It does not establish the real-world accuracy of payment-outlier detection or confirm fraud.

## Reproducible Benchmark Rebuild and Held-Out Evaluation

A separate, reproducible evaluation pipeline was built to address the missing benchmark-generation script in the original project artifacts. This rebuild uses a deterministic provider-level split and is documented separately from the original scoring results.

### Rebuild Design

- **Source:** CMS Medicare Physician & Other Practitioners, 2024.
- **Split strategy:** Deterministic MD5-based assignment by provider NPI, keeping each provider entirely within either the reference or evaluation partition.
- **Reference partition:** 906,084 providers.
- **Evaluation partition:** 301,389 providers.
- **Peer groups:** Provider specialty/type, HCPCS code, and place of service.
- **Benchmark method:** 25th, 50th, and 75th percentiles, with a minimum of 30 reference peer records per group.
- **Reference benchmark groups generated:** 10,884.

### Held-Out Evaluation Results

| Metric | Result |
|---|---:|
| Evaluation records | 2,450,201 |
| Distinct evaluation providers | 301,389 |
| Within-range records | 2,265,116 (92.45%) |
| Low-payment outlier candidates | 83,631 (3.41%) |
| High-payment outlier candidates | 29,075 (1.19%) |
| Records without a reference benchmark | 72,379 (2.95%) |
| Records with missing payment status | 0 |

### Validation Checks

- Each provider was assigned to exactly one partition.
- All 10,884 benchmark groups met the minimum peer-record threshold.
- No benchmark groups had invalid quartiles or missing quartiles.
- All evaluation records received a status.
- No flagged outlier records were missing their benchmark bounds.
- Evaluation status counts reconciled to the total record count.

### Reproduce the Pipeline

Run the following commands from the project root after preparing the processed CMS dataset:

```bash
python src/build_reference_benchmarks.py
python src/create_evaluation_dataset.py
python src/score_provider_payments.py --input data/analytics/rebuild_v1/evaluation_input_2024.parquet --benchmarks data/analytics/rebuild_v1/reference_payment_benchmarks_2024.parquet --output data/analytics/rebuild_v1/evaluation_scored_2024.parquet
python src/summarize_evaluation.py
```

The pipeline writes its rebuild artifacts to `data/analytics/rebuild_v1/`. Generated data artifacts are excluded from Git, so users must run the pipeline locally to reproduce them.

**Interpretation:** Payment outliers are statistical screening signals for further review—not confirmed fraud, improper payments, or recoverable savings. The source contains aggregated provider/service-level data rather than individual adjudicated claims. This provider-level split evaluates separation between reference and evaluation providers within the 2024 dataset; it is not a future-year temporal validation.

The rebuilt benchmark pipeline is a distinct evaluation workflow. Its results should not be interpreted as an exact reconstruction of the original benchmark artifact.


## Machine Learning Anomaly Detection and Stability Analysis

The project compares unsupervised anomaly detection methods to help prioritize provider-service records for analytical review. The experiments evaluate model overlap, feature sensitivity, random-seed stability, and training-sample stability.

### Method Comparison

On a matched evaluation sample of 20,000 records:

| Method | Flagged records | Flag rate |
|---|---:|---:|
| Isolation Forest | 1,532 | 7.66% |
| Local Outlier Factor (LOF) | 1,501 | 7.51% |
| Peer-relative IQR baseline | 929 | 4.65% |

Isolation Forest and LOF agreed on 796 flagged records, with a Jaccard similarity of 35.58%. The methods identify partially different records; these differences do not establish which method is more accurate.

### Stability and Feature Sensitivity

- **Random-seed stability:** Isolation Forest flags changed for 237 of 20,000 records between seeds 42 and 123 (1.185%). LOF flags were unchanged when its training data and configuration were held fixed.
- **Training-sample stability:** Cross-sample Jaccard similarity was 88.07% for Isolation Forest and 65.42% for LOF.
- **Feature ablation:** A matched-record experiment examined the effect of excluding peer-payment features, demonstrating why feature selection and flag stability should be evaluated rather than assumed.

### Payment-Review Candidate Scoring

In a separate completed run, the ML pipeline scored 112,706 records already selected as high- or low-payment outlier candidates. Isolation Forest flagged 28,298 records (25.1%), LOF flagged 26,061 (23.1%), and at least one model flagged 43,899 (38.9%). These rates apply to the preselected candidate population, not all provider-service records.

**Important:** The CMS dataset does not provide confirmed fraud labels. These models generate screening signals for human review; the results do not establish fraud, improper payment, model precision or recall, or recoverable savings.

For experimental configuration, feature definitions, ablation results, stability analyses, validation, and reproducibility instructions, see [ML Anomaly Detection Method Comparison](documentation/ml_anomaly_comparison.md).

## Dashboard Preview

The interactive dashboard helps reviewers explore provider payment patterns, filter potential outliers, inspect provider-level details, and export review worklists.

![Healthcare Payment Integrity Dashboard](documentation/images/dashboard_overview.png)
