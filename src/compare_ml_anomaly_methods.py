"""Compare peer-IQR screening with unsupervised ML anomaly methods.

CMS provider-service aggregates are not confirmed fraud labels.
All anomaly flags are screening signals for human review.
This experiment writes to a separate output directory.
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import LocalOutlierFactor
from sklearn.preprocessing import StandardScaler


FEATURES = [
    "log_beneficiaries",
    "log_services_per_beneficiary",
    "log_bene_days_per_beneficiary",
    "log_charge_to_allowed",
    "payment_to_allowed",
    "payment_to_peer_median",
    "payment_iqr_distance",
    "log_peer_count",
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Compare IQR, Isolation Forest, and LOF."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("data/processed/cms_medicare_provider_service_2024.csv"),
    )
    parser.add_argument(
        "--splits",
        type=Path,
        default=Path(
            "data/analytics/rebuild_v1/provider_split_assignments_2024.parquet"
        ),
    )
    parser.add_argument(
        "--evaluation",
        type=Path,
        default=Path(
            "data/analytics/rebuild_v1/evaluation_scored_2024.parquet"
        ),
    )
    parser.add_argument(
        "--benchmarks",
        type=Path,
        default=Path(
            "data/analytics/rebuild_v1/reference_payment_benchmarks_2024.parquet"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/analytics/ml_comparison_v1"),
    )
    parser.add_argument("--if-train-rows", type=int, default=50000)
    parser.add_argument("--lof-train-rows", type=int, default=10000)
    parser.add_argument("--evaluation-rows", type=int, default=20000)
    parser.add_argument("--contamination", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def feature_sql(alias="x", benchmark_alias=None):
    """Create robust, bounded feature expressions from source columns."""
    benchmark_alias = benchmark_alias or alias
    return f"""
        LN(1 + GREATEST(COALESCE({alias}.Tot_Benes, 0), 0))
            AS log_beneficiaries,
        LN(1 + GREATEST(
            COALESCE({alias}.Tot_Srvcs, 0)
            / GREATEST(COALESCE({alias}.Tot_Benes, 0), 1), 0
        )) AS log_services_per_beneficiary,
        LN(1 + GREATEST(
            COALESCE({alias}.Tot_Bene_Day_Srvcs, 0)
            / GREATEST(COALESCE({alias}.Tot_Benes, 0), 1), 0
        )) AS log_bene_days_per_beneficiary,
        LN(1 + GREATEST(
            COALESCE({alias}.Avg_Sbmtd_Chrg, 0)
            / GREATEST(COALESCE({alias}.Avg_Mdcr_Alowd_Amt, 0.01), 0.01),
            0
        )) AS log_charge_to_allowed,
        LEAST(GREATEST(
            COALESCE({alias}.Avg_Mdcr_Pymt_Amt, 0)
            / GREATEST(COALESCE({alias}.Avg_Mdcr_Alowd_Amt, 0.01), 0.01),
            0
        ), 20) AS payment_to_allowed,
        LEAST(GREATEST(
            COALESCE({alias}.Avg_Mdcr_Pymt_Amt, 0)
            / GREATEST(COALESCE({benchmark_alias}.payment_median, 0.01), 0.01),
            0
        ), 20) AS payment_to_peer_median,
        LEAST(GREATEST(
            (COALESCE({alias}.Avg_Mdcr_Pymt_Amt, 0)
                - COALESCE({benchmark_alias}.payment_median, 0))
            / GREATEST((
                COALESCE({benchmark_alias}.payment_p75, 0)
                - COALESCE({benchmark_alias}.payment_p25, 0)
            ), 0.01),
            -20
        ), 20) AS payment_iqr_distance,
        LN(1 + GREATEST(
            COALESCE({benchmark_alias}.reference_peer_count, 0), 0
        )) AS log_peer_count
    """


def load_reference_sample(con, args):
    source = str(args.source.resolve()).replace("'", "''")
    splits = str(args.splits.resolve()).replace("'", "''")
    benchmarks = str(args.benchmarks.resolve()).replace("'", "''")

    # Deterministic hash filter limits rows materialized in Python.
    # DuckDB scans the CSV; it does not load the full file into pandas.
    query = f"""
        SELECT
            {feature_sql("s", "b")},
            s.Rndrng_NPI AS provider_id
        FROM read_csv_auto('{source}', header=true) s
        INNER JOIN read_parquet('{splits}') a
            ON s.Rndrng_NPI = a.Rndrng_NPI
        INNER JOIN read_parquet('{benchmarks}') b
            ON s.Rndrng_Prvdr_Type = b.Rndrng_Prvdr_Type
            AND s.HCPCS_Cd = b.HCPCS_Cd
            AND s.Place_Of_Srvc = b.Place_Of_Srvc
        WHERE a.split = 'reference'
          AND s.Avg_Mdcr_Pymt_Amt IS NOT NULL
          AND s.Avg_Mdcr_Alowd_Amt IS NOT NULL
          AND s.Tot_Benes IS NOT NULL
          AND hash(
              CAST(s.Rndrng_NPI AS VARCHAR) || '|' ||
              s.HCPCS_Cd || '|' || s.Place_Of_Srvc
          ) % 100 = 0
        ORDER BY hash(
            CAST(s.Rndrng_NPI AS VARCHAR) || '|' ||
            s.HCPCS_Cd || '|' || s.Place_Of_Srvc
        )
        LIMIT {int(args.if_train_rows)}
    """
    return con.execute(query).df()


def load_evaluation_sample(con, args):
    path = str(args.evaluation.resolve()).replace("'", "''")
    query = f"""
        SELECT
            {feature_sql("e")},
            e.Rndrng_NPI AS provider_id,
            e.independent_payment_status
        FROM read_parquet('{path}') e
        WHERE e.Avg_Mdcr_Pymt_Amt IS NOT NULL
          AND e.Avg_Mdcr_Alowd_Amt IS NOT NULL
          AND e.Tot_Benes IS NOT NULL
          AND hash(
              CAST(e.Rndrng_NPI AS VARCHAR) || '|' ||
              e.HCPCS_Cd || '|' || e.Place_Of_Srvc
          ) % 100 = 0
        ORDER BY hash(
            CAST(e.Rndrng_NPI AS VARCHAR) || '|' ||
            e.HCPCS_Cd || '|' || e.Place_Of_Srvc
        )
        LIMIT {int(args.evaluation_rows)}
    """
    return con.execute(query).df()


def prepare_matrix(frame, medians=None):
    matrix = frame[FEATURES].replace([np.inf, -np.inf], np.nan)
    if medians is None:
        medians = matrix.median().fillna(0)
    matrix = matrix.fillna(medians).fillna(0)
    return matrix.to_numpy(dtype=np.float32), medians


def main():
    args = parse_args()

    paths = [args.source, args.splits, args.evaluation, args.benchmarks]
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"Required input not found: {path}")

    if not 0 < args.contamination < 0.5:
        raise ValueError("--contamination must be between 0 and 0.5.")
    if min(args.if_train_rows, args.lof_train_rows, args.evaluation_rows) < 100:
        raise ValueError("Sample sizes must each be at least 100.")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    results_path = output_dir / "ml_comparison_scores_sample.parquet"
    summary_path = output_dir / "ml_comparison_summary.json"

    # Protect previous experiment results from accidental overwrite.
    if results_path.exists() or summary_path.exists():
        raise FileExistsError(
            f"Experiment outputs already exist in {output_dir}. "
            "Choose a new --output-dir to preserve existing results."
        )

    start = time.time()
    con = duckdb.connect(database=":memory:")
    con.execute("SET memory_limit='4GB'")
    con.execute("SET threads=4")

    try:
        print("Loading bounded reference-provider training sample...")
        reference = load_reference_sample(con, args)
        if len(reference) < 100:
            raise RuntimeError(
                f"Only {len(reference)} reference rows found; "
                "check input data and sample filter."
            )

        print(f"Reference sample rows: {len(reference):,}")
        print("Loading held-out evaluation sample...")
        evaluation = load_evaluation_sample(con, args)
        if len(evaluation) < 100:
            raise RuntimeError(
                f"Only {len(evaluation)} evaluation rows found; "
                "check input data and sample filter."
            )

        print(f"Evaluation sample rows: {len(evaluation):,}")

        # Fit preprocessing on reference data only.
        if_train = reference.iloc[:args.if_train_rows].copy()
        lof_train = if_train.iloc[:args.lof_train_rows].copy()

        X_if_train_raw, feature_medians = prepare_matrix(if_train)
        X_eval_raw, _ = prepare_matrix(evaluation, feature_medians)
        scaler_if = StandardScaler()
        X_if_train = scaler_if.fit_transform(X_if_train_raw)
        X_eval_if = scaler_if.transform(X_eval_raw)

        print("Training Isolation Forest...")
        if_model = IsolationForest(
            n_estimators=150,
            contamination=args.contamination,
            random_state=args.seed,
            n_jobs=4,
        )
        if_model.fit(X_if_train)
        if_scores = -if_model.decision_function(X_eval_if)
        if_flags = if_model.predict(X_eval_if) == -1

        # LOF has a separate, smaller training sample and its own scaler.
        print("Training Local Outlier Factor (bounded sample)...")
        X_lof_train_raw, _ = prepare_matrix(lof_train, feature_medians)
        scaler_lof = StandardScaler()
        X_lof_train = scaler_lof.fit_transform(X_lof_train_raw)
        X_eval_lof = scaler_lof.transform(X_eval_raw)

        lof_model = LocalOutlierFactor(
            n_neighbors=20,
            contamination=args.contamination,
            novelty=True,
            n_jobs=1,
        )
        lof_model.fit(X_lof_train)
        lof_scores = -lof_model.decision_function(X_eval_lof)
        lof_flags = lof_model.predict(X_eval_lof) == -1

        output = evaluation[
            ["provider_id", "independent_payment_status"]
        ].copy()
        output["isolation_forest_anomaly_score"] = if_scores
        output["isolation_forest_flag"] = if_flags
        output["lof_anomaly_score"] = lof_scores
        output["lof_flag"] = lof_flags
        output["iqr_outlier_flag"] = output[
            "independent_payment_status"
        ].isin(["low_payment_outlier", "high_payment_outlier"])

        output.to_parquet(results_path, index=False)

        iqr = output["iqr_outlier_flag"].to_numpy(dtype=bool)
        iflag = output["isolation_forest_flag"].to_numpy(dtype=bool)
        lflag = output["lof_flag"].to_numpy(dtype=bool)

        def comparison(flags):
            intersection = int(np.sum(flags & iqr))
            union = int(np.sum(flags | iqr))
            return {
                "flagged_records": int(np.sum(flags)),
                "flagged_rate": float(np.mean(flags)),
                "overlap_with_iqr": intersection,
                "jaccard_overlap_with_iqr": (
                    float(intersection / union) if union else None
                ),
            }

        summary = {
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "experiment": "peer-relative unsupervised anomaly comparison",
            "seed": args.seed,
            "contamination_threshold": args.contamination,
            "reference_sample_rows": int(len(if_train)),
            "lof_training_rows": int(len(lof_train)),
            "evaluation_sample_rows": int(len(evaluation)),
            "evaluation_distinct_providers": int(
                evaluation["provider_id"].nunique()
            ),
            "features": FEATURES,
            "methods": {
                "isolation_forest": comparison(iflag),
                "local_outlier_factor": comparison(lflag),
                "iqr_baseline": {
                    "flagged_records": int(np.sum(iqr)),
                    "flagged_rate": float(np.mean(iqr)),
                },
                "ml_method_overlap": {
                    "isolation_forest_and_lof": int(np.sum(iflag & lflag)),
                    "isolation_forest_lof_jaccard": (
                        float(np.sum(iflag & lflag) / np.sum(iflag | lflag))
                        if np.sum(iflag | lflag) else None
                    ),
                },
            },
            "runtime_seconds": round(time.time() - start, 2),
            "results_file": str(results_path),
            "interpretation": (
                "Unsupervised anomaly flags and IQR outliers are screening "
                "signals only. CMS provider-service aggregates do not provide "
                "confirmed fraud labels; overlap is not fraud accuracy."
            ),
            "limitations": [
                "Initial bounded sample, not full-population ML scoring.",
                "Anomaly rates depend on sample, features, and threshold.",
                "Models may flag legitimate differences in service mix or scale.",
                "LOF scores are relative to its reference training neighborhood.",
            ],
        }
        summary_path.write_text(
            json.dumps(summary, indent=2),
            encoding="utf-8",
        )

        print("\nExperiment complete.")
        print(f"Scores: {results_path}")
        print(f"Summary: {summary_path}")
        print(json.dumps(summary["methods"], indent=2))
        print(f"Runtime: {summary['runtime_seconds']} seconds")

    finally:
        con.close()


if __name__ == "__main__":
    main()
