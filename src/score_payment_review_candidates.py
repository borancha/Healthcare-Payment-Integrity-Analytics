
"""Score payment-review candidates with unsupervised ML models.

Model flags are screening signals for human review, not confirmed fraud.
This pipeline is separate from the existing ML comparison experiment.
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
        description="Score payment-outlier candidates with Isolation Forest and LOF."
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
        default=Path("data/analytics/rebuild_v1/evaluation_scored_2024.parquet"),
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
        default=Path("data/analytics/payment_review_ml_v1"),
    )
    parser.add_argument("--if-train-rows", type=int, default=50000)
    parser.add_argument("--lof-train-rows", type=int, default=10000)
    parser.add_argument("--contamination", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--reference-sample-seed", type=int, default=42)
    return parser.parse_args()

def feature_sql(alias="x", benchmark_alias=None):
    """Build the same eight bounded features as the comparison experiment."""
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


def prepare_matrix(frame, medians=None):
    matrix = frame[FEATURES].replace([np.inf, -np.inf], np.nan)
    if medians is None:
        medians = matrix.median().fillna(0)
    matrix = matrix.fillna(medians).fillna(0)
    return matrix.to_numpy(dtype=np.float32), medians


def load_reference_sample(con, args):
    source = str(args.source.resolve()).replace("'", "''")
    splits = str(args.splits.resolve()).replace("'", "''")
    benchmarks = str(args.benchmarks.resolve()).replace("'", "''")

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
              CAST({int(args.reference_sample_seed)} AS VARCHAR) || '|' ||
              CAST(s.Rndrng_NPI AS VARCHAR) || '|' ||
              s.HCPCS_Cd || '|' || s.Place_Of_Srvc
          ) % 100 = 0
        ORDER BY hash(
            CAST({int(args.reference_sample_seed)} AS VARCHAR) || '|' ||
            CAST(s.Rndrng_NPI AS VARCHAR) || '|' ||
            s.HCPCS_Cd || '|' || s.Place_Of_Srvc
        )
        LIMIT {int(args.if_train_rows)}
    """
    return con.execute(query).df()


def load_candidates(con, args):
    evaluation = str(args.evaluation.resolve()).replace("'", "''")

    query = f"""
        SELECT
            e.Rndrng_NPI AS provider_id,
            e.Rndrng_Prvdr_State_Abrvtn AS provider_state,
            e.Rndrng_Prvdr_Type AS provider_type,
            e.HCPCS_Cd AS hcpcs_code,
            e.HCPCS_Desc AS procedure_description,
            e.Place_Of_Srvc AS place_of_service,
            e.independent_payment_status,
            e.Avg_Mdcr_Pymt_Amt AS avg_medicare_payment,
            e.reference_peer_count,
            e.payment_p25,
            e.payment_median,
            e.payment_p75,
            e.lower_payment_bound,
            e.upper_payment_bound,
            {feature_sql("e")}
        FROM read_parquet('{evaluation}') e
        WHERE e.independent_payment_status IN (
            'high_payment_outlier', 'low_payment_outlier'
        )
          AND e.Avg_Mdcr_Pymt_Amt IS NOT NULL
          AND e.Avg_Mdcr_Alowd_Amt IS NOT NULL
          AND e.Tot_Benes IS NOT NULL
    """
    return con.execute(query).df()

def main():
    args = parse_args()

    if min(args.if_train_rows, args.lof_train_rows) < 100:
        raise ValueError("Training sample sizes must each be at least 100.")
    if args.lof_train_rows > args.if_train_rows:
        raise ValueError("--lof-train-rows cannot exceed --if-train-rows.")
    if not 0 < args.contamination < 0.5:
        raise ValueError("--contamination must be between 0 and 0.5.")

    for path in (args.source, args.splits, args.evaluation, args.benchmarks):
        if not path.exists():
            raise FileNotFoundError(f"Required input not found: {path}")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    scores_path = output_dir / "payment_review_ml_scores.parquet"
    summary_path = output_dir / "payment_review_ml_summary.json"

    if scores_path.exists() or summary_path.exists():
        raise FileExistsError(
            f"Output files already exist in {output_dir}. "
            "Choose another --output-dir to protect previous results."
        )

    started = time.time()
    con = duckdb.connect(database=":memory:")
    con.execute("SET memory_limit='4GB'")
    con.execute("SET threads=4")

    try:
        print("Loading reference-provider training sample...")
        reference = load_reference_sample(con, args)
        if len(reference) < args.lof_train_rows:
            raise RuntimeError(
                f"Only {len(reference):,} reference rows loaded; "
                f"at least {args.lof_train_rows:,} are required."
            )

        print(f"Reference training rows: {len(reference):,}")
        lof_train = reference.iloc[:args.lof_train_rows].copy()

        print("Loading payment-outlier candidates...")
        candidates = load_candidates(con, args)
        if candidates.empty:
            raise RuntimeError("No eligible payment-review candidates found.")

        print(f"Eligible candidates: {len(candidates):,}")

        # Fit missing-value handling and scaling using reference data only.
        X_if_raw, medians = prepare_matrix(reference)
        X_candidates_raw, _ = prepare_matrix(candidates, medians)

        scaler_if = StandardScaler()
        X_if_train = scaler_if.fit_transform(X_if_raw)
        X_if_candidates = scaler_if.transform(X_candidates_raw)

        print("Training Isolation Forest...")
        if_model = IsolationForest(
            n_estimators=150,
            contamination=args.contamination,
            random_state=args.seed,
            n_jobs=4,
        )
        if_model.fit(X_if_train)
        if_scores = -if_model.decision_function(X_if_candidates)
        if_flags = if_model.predict(X_if_candidates) == -1

        # LOF uses its own scaler and bounded reference sample.
        X_lof_raw, _ = prepare_matrix(lof_train, medians)
        scaler_lof = StandardScaler()
        X_lof_train = scaler_lof.fit_transform(X_lof_raw)
        X_lof_candidates = scaler_lof.transform(X_candidates_raw)

        print("Training Local Outlier Factor...")
        lof_model = LocalOutlierFactor(
            n_neighbors=20,
            contamination=args.contamination,
            novelty=True,
            n_jobs=1,
        )
        lof_model.fit(X_lof_train)
        lof_scores = -lof_model.decision_function(X_lof_candidates)
        lof_flags = lof_model.predict(X_lof_candidates) == -1

        output = candidates.copy()
        output["isolation_forest_anomaly_score"] = if_scores
        output["isolation_forest_flag"] = if_flags
        output["lof_anomaly_score"] = lof_scores
        output["lof_flag"] = lof_flags
        output["both_ml_methods_flagged"] = if_flags & lof_flags
        output["either_ml_method_flagged"] = if_flags | lof_flags

        output.to_parquet(scores_path, index=False)

        summary = {
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "purpose": "Payment-outlier candidate screening for human review",
            "warning": (
                "ML flags are screening signals, not confirmed fraud findings."
            ),
            "source_evaluation": str(args.evaluation),
            "reference_sample_seed": args.reference_sample_seed,
            "model_seed": args.seed,
            "contamination": args.contamination,
            "reference_training_rows": int(len(reference)),
            "isolation_forest_training_rows": int(len(reference)),
            "lof_training_rows": int(len(lof_train)),
            "candidate_rows_scored": int(len(output)),
            "isolation_forest_flagged": int(np.sum(if_flags)),
            "lof_flagged": int(np.sum(lof_flags)),
            "both_methods_flagged": int(np.sum(if_flags & lof_flags)),
            "either_method_flagged": int(np.sum(if_flags | lof_flags)),
            "payment_status_counts": {
                str(key): int(value)
                for key, value in output[
                    "independent_payment_status"
                ].value_counts().items()
            },
            "elapsed_seconds": round(time.time() - started, 2),
            "scores_file": scores_path.name,
        }

        with summary_path.open("w", encoding="utf-8") as file:
            json.dump(summary, file, indent=2)

        print("\nScoring completed.")
        print(json.dumps(summary, indent=2))
        print(f"\nScores:  {scores_path}")
        print(f"Summary: {summary_path}")

    finally:
        con.close()


if __name__ == "__main__":
    main()
