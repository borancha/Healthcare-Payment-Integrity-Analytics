# Medicare Provider Payment Outlier Scoring Pipeline

## 1. Project purpose

This pipeline screens Medicare Physician and Other Practitioners
provider-service records for statistically unusual average Medicare
payment amounts relative to comparable peer groups.

The purpose is to prioritize records for further analytical or
program-integrity review. An outlier is not proof of fraud, waste,
abuse, an improper payment, or recoverable savings.

## 2. Data source and grain

Source: CMS Medicare Physician & Other Practitioners — by Provider and Service.

Official collection:
https://data.cms.gov/provider-summary-by-type-of-service/medicare-physician-other-practitioners

Data dictionary:
https://data.cms.gov/resources/medicare-physician-other-practitioners-by-provider-and-service-data-dictionary

The dataset contains aggregate provider-service records, not individual
claim transactions. Average payment values must not be described as
total payments or total financial exposure.

The dataset includes service-code and place-of-service context. Service
units can vary by HCPCS code, so service counts should not automatically
be interpreted as visits or procedures.

## 3. Peer-group design

The primary peer-group keys are:

- Rendering provider type
- HCPCS code
- Place of service

Reference benchmarks are calculated from a separate provider reference
split. Only peer groups with at least 30 reference records are retained.

For each peer group, the benchmark stores:

- Reference peer count
- 25th percentile of average Medicare payment
- Median average Medicare payment
- 75th percentile of average Medicare payment

The saved 2024 reference benchmark artifact contains 11,250 peer groups.
Its minimum peer count is 30.

## 4. Scoring rule

For each matched record, calculate:

IQR = P75 - P25

Lower bound = P25 - 1.5 × IQR

Upper bound = P75 + 1.5 × IQR

Classification:

- `high_payment_outlier`: payment is above the upper bound
- `low_payment_outlier`: payment is below the lower bound
- `within_range`: payment falls within the bounds
- `not_scored_no_reference_benchmark`: no exact benchmark match exists
- `not_scored_missing_payment`: payment amount is missing

The current reusable script uses the fixed saved reference benchmark table.
It does not rebuild benchmarks from the records being scored.

## 5. Validation performed

Reference benchmark checks:

- 11,250 total benchmark groups
- 11,250 unique peer groups
- No groups below the 30-peer minimum
- No invalid quartile ordering
- No missing quartile values

Evaluation artifact:

- 1,946,987 records
- 1,893,433 records with sufficient reference benchmarks
- 89,772 high- or low-payment outlier records
- 97.25% scored coverage

Reusable script smoke test:

- 10,000 input records
- 10,000 output records
- 9,312 within range
- 309 without an exact reference benchmark
- 279 low-payment outliers
- 100 high-payment outliers

The smoke test passed row-count and payment-bound integrity checks.

## 6. Important limitations

1. Statistical outlier status is a review signal, not a determination of
   improper payment or provider misconduct.
2. The source data is aggregated at provider-service level and does not
   contain the complete detail needed to adjudicate individual claims.
3. Provider type, HCPCS code, and place of service improve comparability,
   but do not account for every legitimate clinical, geographic, coding,
   contractual, or operational difference.
4. Some HCPCS codes represent heterogeneous services or drug units.
   Code descriptions and payment units require domain validation.
5. Records without a matching peer benchmark are left unscored rather
   than silently compared with a mismatched peer group.
6. The reference benchmarks are specific to the 2024 source dataset.
   They should not be assumed valid for later years without monitoring
   and validation.
7. The evaluation split must remain independent of model and threshold
   tuning. Any operational severity thresholds should be selected using
   development/reference data, frozen, and then tested on untouched data.
8. Average payment deviations are not total dollars at risk and cannot
   be presented as savings, overpayments, or recoveries.

## 7. Recommended future improvements

- Add unit tests for peer matching, missing values, and boundary cases.
- Add data-quality checks for source schema and duplicate benchmark keys.
- Save a run manifest with source path, source year, benchmark version,
  execution timestamp, input row count, output row count, and status counts.
- Add a dashboard that shows outlier counts, peer support, payment
  deviation, and review status.
- Validate future-year performance using a time-based holdout.
- Add an auditable human-review workflow before any compliance conclusion.
- Consider additional features only when their meaning and comparability
  are defensible for the source data.

## 8. Intended use

This is a portfolio analytics prototype designed to demonstrate
data engineering, peer benchmarking, statistical anomaly screening,
validation, and responsible interpretation of healthcare payment data.
It is not a production compliance system or a substitute for CMS policy,
claims adjudication, or expert review.


## Reviewer Prioritization — Exploratory Rules

### Purpose

The dashboard assigns a transparent review priority to statistical payment outliers. The purpose is to help a human reviewer organize a work queue, not to predict fraud or determine whether a payment was improper.

### Initial rules

- **Priority 1:** Absolute distance beyond the applicable payment boundary is at least $100, with at least 200 reference peer records.
- **Priority 2:** Absolute distance beyond the applicable payment boundary is at least $25, with at least 50 reference peer records, when Priority 1 does not apply.
- **Standard:** The candidate does not meet the higher-priority thresholds and has at least 50 reference peer records.
- **Review benchmark support:** The reference peer group contains fewer than 50 records and no higher priority applies.

The distance is calculated above the upper payment bound for high-payment outliers and below the lower payment bound for low-payment outliers.

### Limitations

These thresholds are exploratory and have not been validated against confirmed improper-payment outcomes. They should not be interpreted as fraud probabilities, evidence of provider misconduct, or estimates of recoverable savings.

Peer-group size is a measure of benchmark support, not proof that the benchmark is clinically appropriate. Payment differences may reflect legitimate geographic, clinical, coding, service-mix, or other factors.

Before operational use, thresholds should be calibrated on development data, frozen before evaluation, and assessed for stability, review workload, false-positive burden, and performance across relevant provider and service groups.

## Review-Priority Validation — 2024 Evaluation Dataset

### Purpose

The review-priority rules organize statistical payment outliers into a transparent human-review queue. They do not estimate fraud probability or determine whether a payment was improper.

### Validation Results

The priority logic was independently recalculated across all 89,772 high- and low-payment outlier records in the 2024 evaluation dataset.

| Validation metric | Result |
|---|---:|
| Total outlier candidates | 89,772 |
| Candidates with invalid distance calculations | 0 |
| Candidates with fewer than 30 reference peers | 0 |
| Priority 1 | 937 |
| Priority 2 | 6,733 |
| Standard | 80,531 |
| Review benchmark support | 1,571 |

The four priority categories sum to 89,772, matching the total candidate count. No candidate had a non-positive distance beyond its applicable payment boundary, and all candidates had at least 30 reference peers.

### Interpretation

These results support the internal consistency of the implemented rules on the evaluation dataset. They do not establish that the thresholds identify improper payments effectively, nor do they measure sensitivity, precision, false-positive rates, or financial recoverability.

The priority thresholds remain exploratory. Before operational use, they should be calibrated on development data, frozen before evaluation, and assessed using appropriate outcome labels, review workload, stability, and performance across provider and service groups.

### Validation Artifact

The metric-level results are stored in:

`data/analytics/evaluation/review_priority_validation_2024.csv`

### Limitations

CMS provider/service records contain aggregate payment and utilization information, not individual claim adjudication records. A statistical payment outlier is a candidate for further review, not evidence of fraud, provider misconduct, or confirmed improper payment.

