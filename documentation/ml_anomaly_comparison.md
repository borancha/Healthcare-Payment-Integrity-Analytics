# ML Anomaly Detection Method Comparison

## 1. Purpose

This experiment compares two unsupervised machine-learning methods, Isolation Forest and Local Outlier Factor (LOF), against an Interquartile Range (IQR) payment-outlier baseline using the 2024 CMS Medicare Physician and Other Practitioners by Provider and Service dataset.

The objective is to evaluate how the methods identify different records for potential review. This experiment does not establish fraud, improper payments, or recoverable savings.

## 2. Data Source and Grain

- **Source:** CMS Medicare Physician and Other Practitioners by Provider and Service, 2024.
- **Data grain:** Provider, HCPCS code, and place of service.
- **Evaluation sample:** 20,000 records selected deterministically from the held-out evaluation dataset.
- **Distinct providers in evaluation sample:** 18,518.

This is aggregated provider-service data, not individual claim-level data. The dataset does not provide confirmed fraud labels.

## 3. Experimental Configuration

| Setting | Value |
|---|---:|
| Random seed | 42 |
| Maximum Isolation Forest training rows | 50,000 |
| Maximum LOF training rows | 10,000 |
| Evaluation rows | 20,000 |
| Isolation Forest estimators | 150 |
| LOF neighbors | 20 |
| Configured contamination | 0.05 |
| IQR minimum peer-group size | 30 |

The configured contamination is a model parameter, not an estimate of the actual fraud rate. Evaluation flag rates can differ from the contamination parameter.

## 4. Feature Engineering

The experiment uses the following features:

- Log-transformed beneficiary count.
- Log-transformed services per beneficiary.
- Log-transformed beneficiary-day services per beneficiary.
- Log-transformed submitted-charge-to-allowed-amount ratio.
- Medicare payment-to-allowed-amount ratio.
- Payment relative to the peer-group median.
- Payment distance relative to peer-group IQR.
- Log-transformed peer-group count.

Missing and infinite feature values are handled before model scoring. Feature medians and scaling parameters are learned from training data and reused for evaluation.

**Important limitation:** The ML feature set includes peer-benchmark-derived information. Therefore, the ML comparison is peer-aware and is not an independent test of payment benchmarking.

## 5. Methods

### Isolation Forest

Isolation Forest identifies observations that are easier to isolate from the broader feature distribution. The experiment uses 150 estimators and a random seed of 42.

### Local Outlier Factor

LOF evaluates local density differences relative to neighboring observations. The experiment uses 20 neighbors and novelty detection to score the held-out evaluation records against the fitted training reference.

### IQR Baseline

The benchmark baseline uses the peer-group payment distribution:

- IQR = P75 - P25
- Lower bound = P25 - 1.5 × IQR
- Upper bound = P75 + 1.5 × IQR

Payments outside these bounds are flagged as low-payment or high-payment outliers. Benchmark construction applies a minimum peer-group size of 30.

## 6. Main Experiment Results

| Method or comparison | Flagged records / overlap | Rate / Jaccard |
|---|---:|---:|
| Isolation Forest | 1,532 flagged | 7.660% |
| Local Outlier Factor | 1,501 flagged | 7.505% |
| IQR baseline | 929 flagged | 4.645% |
| Isolation Forest and LOF overlap | 796 records | 35.58% Jaccard |
| Isolation Forest and IQR overlap | 268 records | 12.22% Jaccard |
| LOF and IQR overlap | 233 records | 10.61% Jaccard |

Jaccard similarity is the size of the intersection divided by the size of the union of two flagged-record sets.

The results show that the methods flag partially different records. The ML methods flagged more records than the IQR baseline in this sample. These differences alone do not establish that one method is more accurate.

## 7. Output Validation

The output file was validated using DuckDB.

| Validation check | Result |
|---|---:|
| Total evaluation rows | 20,000 |
| Distinct providers | 18,518 |
| Invalid Isolation Forest scores | 0 |
| Invalid LOF scores | 0 |
| Isolation Forest flags | 1,532 |
| LOF flags | 1,501 |
| IQR flags | 929 |
| Isolation Forest / LOF overlap | 796 |
| Isolation Forest / IQR overlap | 268 |
| LOF / IQR overlap | 233 |

The counts agree with the experiment summary JSON.

## 8. Output Artifacts

The main experiment writes its outputs to `data/analytics/ml_comparison_v1/`:

- `ml_comparison_scores_sample.parquet` — record-level evaluation scores and flags.
- `ml_comparison_summary.json` — summary counts and method-overlap metrics.

The separate pilot experiment is stored under `data/analytics/ml_comparison_pilot_v1/` and should not be confused with the main experiment.

## 9. Reproducibility

Run the main experiment from the repository root with the project's Conda environment activated:

```bash
python src/compare_ml_anomaly_methods.py --if-train-rows 50000 --lof-train-rows 10000 --evaluation-rows 20000 --output-dir data/analytics/ml_comparison_v1
```

## 10. Limitations and Responsible Interpretation

1. The CMS dataset contains aggregated provider-service data, not individual claims.
2. Confirmed fraud labels are not available for this experiment; therefore, fraud-detection accuracy, precision, and recall have not been established.
3. Statistical and ML outliers are signals for review, not proof of fraud, abuse, waste, or improper payment.
4. An outlier does not establish recoverable payments or realized savings.
5. The 20,000-row evaluation sample contains 18,518 distinct providers, so some providers have multiple records.
6. The ML methods use peer-benchmark-derived features, meaning their results are not independent of the benchmark context.
7. Results depend on sampling, feature engineering, model parameters, and the evaluation period.
8. This is a portfolio prototype, not a production payment-integrity or compliance system.

## 11. Recommended Next Steps

- Run a feature-ablation experiment excluding peer-median and IQR-distance features.
- Evaluate score and flag stability across multiple deterministic samples and random seeds.
- Review flagged records by provider type, HCPCS code, place of service, and geography.
- Add unit tests for feature preparation, scoring, and overlap calculations.
- If credible adjudicated labels become available, evaluate precision, recall, and precision-recall curves.
- Integrate model results into the dashboard as clearly labeled review-prioritization signals.

## 12. Intended Use

This comparison demonstrates a workflow for comparing unsupervised anomaly-detection methods with a transparent statistical baseline. It supports further analysis and human review; it does not automate payment denials or establish fraud.


## 13. Feature Ablation and Matched-Record Comparison

### Objective

Compare the full eight-feature model against an ablation model that excludes `payment_to_peer_median` and `payment_iqr_distance`. The remaining six features include `log_peer_count`.

Both experiments use the same deterministic evaluation sample of 20,000 rows, with 50,000 Isolation Forest training rows, 10,000 Local Outlier Factor training rows, contamination set to 0.05, and random seed 42.

### Results

| Metric | Full features | Without peer-payment features |
|---|---:|---:|
| Isolation Forest flagged | 1,532 (7.66%) | 1,085 (5.43%) |
| Local Outlier Factor flagged | 1,501 (7.51%) | 1,537 (7.69%) |
| IQR baseline flagged | 929 (4.65%) | 929 (4.65%) |
| IF/LOF overlap | 796 | 332 |
| IF/LOF Jaccard similarity | 35.58% | 14.50% |

### Matched-record stability

The two model configurations were compared using the same 20,000 evaluation records, matched by provider ID, HCPCS code, and place of service. All records matched.

- **Isolation Forest:** 1,009 records (5.05%) changed flag status; 728 were flagged only by the full-feature model, and 281 only by the ablation model.
- **Local Outlier Factor:** 970 records (4.85%) changed flag status; 467 were flagged only by the full-feature model, and 503 only by the ablation model.

### Interpretation and limitations

Removing peer-payment features materially changed anomaly flags and reduced agreement between Isolation Forest and Local Outlier Factor. This demonstrates that model results are sensitive to feature selection.

These experiments do not establish fraud-detection accuracy. The CMS dataset contains aggregated provider-service records, not individual claims, and there are no confirmed fraud labels. Flags are screening signals for further review, not evidence of fraud, improper payment, or recoverable savings.

### Reproducibility

Full-feature experiment:

```bash
python src/compare_ml_anomaly_methods.py --feature-set full --if-train-rows 50000 --lof-train-rows 10000 --evaluation-rows 20000 --output-dir data/analytics/ml_comparison_matched_v2
```

Ablation experiment:

```bash
python src/compare_ml_anomaly_methods.py --feature-set no-peer-payment --if-train-rows 50000 --lof-train-rows 10000 --evaluation-rows 20000 --output-dir data/analytics/ml_ablation_matched_v2
```

The script intentionally refuses to overwrite existing experiment outputs. Use a new output directory for any rerun.

## 14. Random-Seed Stability Analysis

### Objective

Evaluate whether anomaly scores and binary flags change when the Isolation Forest random seed changes while keeping the evaluation records, training records, feature set, contamination threshold, and sample sizes fixed.

### Experimental configuration

- Feature set: full eight-feature configuration.
- Isolation Forest training sample: 50,000 records.
- LOF training sample: 10,000 records.
- Evaluation sample: 20,000 records.
- Contamination: 0.05.
- Seeds compared: 42 and 123.
- Evaluation records matched using provider ID, HCPCS code, and place of service.

### Results

| Metric | Isolation Forest | Local Outlier Factor |
|---|---:|---:|
| Flags changed between seeds | 237 | 0 |
| Flag changes as percentage of evaluation records | 1.185% | 0.00% |
| Score correlation between runs | 0.9833 | 1.0000 |

For Isolation Forest, 116 records were flagged only under seed 42 and 121 only under seed 123. Both runs evaluated the same 20,000 records. The IQR baseline was unchanged.

### Interpretation

Isolation Forest scores were highly correlated across seeds, although 237 records changed binary flag status. This indicates that overall score ordering was broadly similar while some observations near the decision threshold were sensitive to random initialization.

LOF produced identical scores and flags because its training data, evaluation data, preprocessing, and model configuration were unchanged. This is a reproducibility check under fixed data, not evidence of generalization to other samples.

### Limitations

Only two random seeds were tested. The experiment does not evaluate variation caused by different training samples, evaluation periods, or feature distributions. Correlation does not measure fraud-detection accuracy, and there are no confirmed fraud labels in this dataset. All anomaly flags remain screening signals for human review.
