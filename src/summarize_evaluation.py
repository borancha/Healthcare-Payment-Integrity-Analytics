
"""Summarize held-out Medicare payment anomaly screening results."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarize a scored evaluation Parquet file."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(
            "data/analytics/rebuild_v1/evaluation_scored_2024.parquet"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/analytics/rebuild_v1/evaluation_summary_2024.json"
        ),
    )
    args = parser.parse_args()

    input_path = args.input.resolve()
    output_path = args.output.resolve()

    if not input_path.exists():
        raise FileNotFoundError(f"Scored evaluation file not found: {input_path}")
    if input_path == output_path:
        raise ValueError("Summary output must differ from the scored input.")
    if output_path.exists():
        raise FileExistsError(
            f"Summary already exists: {output_path}. "
            "Choose a new output path to avoid overwriting it."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(database=":memory:")

    try:
        totals = con.execute(
            """
            SELECT
                COUNT(*) AS total_records,
                COUNT(DISTINCT Rndrng_NPI) AS distinct_providers,
                COUNT(*) FILTER (
                    WHERE independent_payment_status = 'within_range'
                ) AS within_range,
                COUNT(*) FILTER (
                    WHERE independent_payment_status = 'low_payment_outlier'
                ) AS low_payment_outlier,
                COUNT(*) FILTER (
                    WHERE independent_payment_status = 'high_payment_outlier'
                ) AS high_payment_outlier,
                COUNT(*) FILTER (
                    WHERE independent_payment_status =
                        'not_scored_no_reference_benchmark'
                ) AS no_reference_benchmark,
                COUNT(*) FILTER (
                    WHERE independent_payment_status =
                        'not_scored_missing_payment'
                ) AS missing_payment,
                COUNT(*) FILTER (
                    WHERE independent_payment_status IS NULL
                ) AS missing_status,
                COUNT(*) FILTER (
                    WHERE independent_payment_status IN (
                        'high_payment_outlier', 'low_payment_outlier'
                    )
                    AND (
                        lower_payment_bound IS NULL
                        OR upper_payment_bound IS NULL
                    )
                ) AS outliers_missing_bounds
            FROM read_parquet(?)
            """,
            [str(input_path)],
        ).fetchone()

        (
            total_records,
            distinct_providers,
            within_range,
            low_outlier,
            high_outlier,
            no_benchmark,
            missing_payment,
            missing_status,
            outliers_missing_bounds,
        ) = map(int, totals)

        def rate(count: int) -> float:
            return round(count / total_records * 100, 4) if total_records else 0.0

        summary = {
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "input_file": str(input_path),
            "dataset": "CMS Medicare Physician & Other Practitioners, 2024",
            "method": "Provider-level deterministic holdout; peer-relative 1.5-IQR screening",
            "counts": {
                "total_records": total_records,
                "distinct_providers": distinct_providers,
                "within_range": within_range,
                "low_payment_outlier": low_outlier,
                "high_payment_outlier": high_outlier,
                "not_scored_no_reference_benchmark": no_benchmark,
                "not_scored_missing_payment": missing_payment,
                "missing_status": missing_status,
                "outliers_missing_bounds": outliers_missing_bounds,
            },
            "rates_percent_of_all_records": {
                "within_range": rate(within_range),
                "low_payment_outlier": rate(low_outlier),
                "high_payment_outlier": rate(high_outlier),
                "no_reference_benchmark": rate(no_benchmark),
                "missing_payment": rate(missing_payment),
            },
            "validation": {
                "status_counts_sum_to_total": (
                    within_range
                    + low_outlier
                    + high_outlier
                    + no_benchmark
                    + missing_payment
                    == total_records
                ),
                "no_missing_status": missing_status == 0,
                "no_outliers_missing_bounds": outliers_missing_bounds == 0,
            },
            "interpretation": (
                "Statistical payment outliers are candidates for human review. "
                "They are not confirmed fraud, improper payments, or recoverable savings."
            ),
            "limitations": [
                "CMS records are aggregated provider-service observations, not individual adjudicated claims.",
                "Average Medicare payment is not total payment.",
                "The evaluation uses a deterministic provider-level split within 2024 data, not a future-year temporal holdout.",
                "No confirmed fraud or improper-payment labels are available in this evaluation.",
                "This new benchmark build does not reproduce the original benchmark artifact.",
            ],
        }

        if not all(summary["validation"].values()):
            raise RuntimeError(
                f"Evaluation validation failed: {summary['validation']}"
            )

        output_path.write_text(
            json.dumps(summary, indent=2),
            encoding="utf-8",
        )

        print("Evaluation summary created.")
        print(f"Output: {output_path}")
        print(f"Total records: {total_records:,}")
        print(f"Distinct providers: {distinct_providers:,}")
        print(f"Low payment outliers: {low_outlier:,} ({rate(low_outlier):.2f}%)")
        print(f"High payment outliers: {high_outlier:,} ({rate(high_outlier):.2f}%)")
        print(f"No reference benchmark: {no_benchmark:,} ({rate(no_benchmark):.2f}%)")
        print(f"Validation: {summary['validation']}")

    finally:
        con.close()


if __name__ == "__main__":
    main()
