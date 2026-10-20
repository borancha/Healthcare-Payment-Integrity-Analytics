
"""Build reproducible provider-level reference payment benchmarks.

This creates a NEW benchmark artifact. It does not reproduce or overwrite
the original benchmark. Statistical outliers are review candidates, not
confirmed fraud or improper payments.
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb


PEER_KEYS = [
    "Rndrng_Prvdr_Type",
    "HCPCS_Cd",
    "Place_Of_Srvc",
]

SPLIT_RULE = (
    "Provider-level deterministic split using MD5 of Rndrng_NPI as text. "
    "First hexadecimal character 0-3 = evaluation; 4-f = reference. "
    "This gives approximately 25% evaluation and 75% reference providers."
)


def sql_literal(value: Path) -> str:
    """Safely quote a filesystem path as a SQL string literal."""
    return "'" + str(value.resolve()).replace("'", "''") + "'"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a new reproducible reference benchmark artifact."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(
            "data/processed/cms_medicare_provider_service_2024.csv"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/analytics/rebuild_v1"),
    )
    args = parser.parse_args()

    input_path = args.input.resolve()
    output_dir = args.output_dir.resolve()

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    output_dir.mkdir(parents=True, exist_ok=True)

    benchmark_path = output_dir / "reference_payment_benchmarks_2024.parquet"
    assignments_path = output_dir / "provider_split_assignments_2024.parquet"
    manifest_path = output_dir / "benchmark_build_manifest_2024.json"

    protected_paths = {
        Path("data/analytics/evaluation/reference_payment_benchmarks_2024.parquet").resolve(),
        Path("data/analytics/provider_payment_anomaly_scores_2024.parquet").resolve(),
        Path("data/analytics/provider_payment_anomaly_scores_2024_v2.parquet").resolve(),
    }

    if benchmark_path in protected_paths or assignments_path in protected_paths:
        raise ValueError("Output path conflicts with an existing protected artifact.")

    if benchmark_path.exists() or assignments_path.exists() or manifest_path.exists():
        raise FileExistsError(
            f"An output already exists in {output_dir}. "
            "Choose a new output directory to avoid overwriting files."
        )

    source = f"read_csv_auto({sql_literal(input_path)}, header=true)"
    con = duckdb.connect(database=":memory:")

    try:
        print("Checking required source columns...")
        columns = {
            row[0]
            for row in con.execute(
                f"DESCRIBE SELECT * FROM {source}"
            ).fetchall()
        }

        required = set(PEER_KEYS) | {
            "Rndrng_NPI",
            "Avg_Mdcr_Pymt_Amt",
        }
        missing = sorted(required - columns)
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

        print("Writing deterministic provider split assignments...")
        con.execute(
            f"""
            COPY (
                SELECT DISTINCT
                    Rndrng_NPI,
                    CASE
                        WHEN substr(
                            md5(CAST(Rndrng_NPI AS VARCHAR)), 1, 1
                        ) IN ('0', '1', '2', '3')
                        THEN 'evaluation'
                        ELSE 'reference'
                    END AS split
                FROM {source}
                WHERE Rndrng_NPI IS NOT NULL
            )
            TO {sql_literal(assignments_path)}
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )

        print("Building peer-group payment benchmarks...")
        con.execute(
            f"""
            COPY (
                SELECT
                    Rndrng_Prvdr_Type,
                    HCPCS_Cd,
                    Place_Of_Srvc,
                    COUNT(*) AS reference_peer_count,
                    quantile_cont(Avg_Mdcr_Pymt_Amt, 0.25) AS payment_p25,
                    quantile_cont(Avg_Mdcr_Pymt_Amt, 0.50) AS payment_median,
                    quantile_cont(Avg_Mdcr_Pymt_Amt, 0.75) AS payment_p75
                FROM {source}
                WHERE Rndrng_NPI IS NOT NULL
                  AND substr(
                      md5(CAST(Rndrng_NPI AS VARCHAR)), 1, 1
                  ) NOT IN ('0', '1', '2', '3')
                  AND Avg_Mdcr_Pymt_Amt IS NOT NULL
                  AND Rndrng_Prvdr_Type IS NOT NULL
                  AND HCPCS_Cd IS NOT NULL
                  AND Place_Of_Srvc IS NOT NULL
                GROUP BY
                    Rndrng_Prvdr_Type,
                    HCPCS_Cd,
                    Place_Of_Srvc
                HAVING COUNT(*) >= 30
            )
            TO {sql_literal(benchmark_path)}
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )

        print("Validating output artifacts...")
        assignment_stats = con.execute(
            """
            SELECT
                COUNT(*) AS distinct_providers,
                COUNT(*) FILTER (WHERE split = 'reference') AS reference_providers,
                COUNT(*) FILTER (WHERE split = 'evaluation') AS evaluation_providers
            FROM read_parquet(?)
            """,
            [str(assignments_path)],
        ).fetchone()

        benchmark_stats = con.execute(
            """
            SELECT
                COUNT(*) AS benchmark_groups,
                MIN(reference_peer_count) AS min_peer_count,
                COUNT(*) FILTER (
                    WHERE payment_p25 > payment_median
                       OR payment_median > payment_p75
                ) AS invalid_quartile_groups,
                COUNT(*) FILTER (
                    WHERE payment_p25 IS NULL
                       OR payment_median IS NULL
                       OR payment_p75 IS NULL
                ) AS groups_with_missing_quartiles
            FROM read_parquet(?)
            """,
            [str(benchmark_path)],
        ).fetchone()

        manifest = {
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "source_file": str(input_path),
            "split_rule": SPLIT_RULE,
            "peer_keys": PEER_KEYS,
            "minimum_reference_records_per_peer_group": 30,
            "quantile_method": "DuckDB quantile_cont",
            "reference_split": {
                "distinct_providers": assignment_stats[1],
            },
            "provider_split_counts": {
                "distinct_providers_total": assignment_stats[0],
                "reference_providers": assignment_stats[1],
                "evaluation_providers": assignment_stats[2],
            },
            "benchmark_validation": {
                "benchmark_groups": benchmark_stats[0],
                "minimum_peer_count": benchmark_stats[1],
                "invalid_quartile_groups": benchmark_stats[2],
                "groups_with_missing_quartiles": benchmark_stats[3],
            },
            "benchmark_file": str(benchmark_path),
            "provider_assignments_file": str(assignments_path),
            "interpretation": (
                "Statistical payment outliers are review candidates, "
                "not confirmed fraud, improper payments, or recoverable savings."
            ),
            "reconstruction_note": (
                "This is a new deterministic split and benchmark build; "
                "it is not claimed to reproduce the original benchmark."
            ),
        }

        manifest_path.write_text(
            json.dumps(manifest, indent=2),
            encoding="utf-8",
        )

        print("\nBuild complete.")
        print(f"Benchmark file: {benchmark_path}")
        print(f"Provider assignments: {assignments_path}")
        print(f"Manifest: {manifest_path}")
        print(f"\nDistinct providers: {assignment_stats[0]:,}")
        print(f"Reference providers: {assignment_stats[1]:,}")
        print(f"Evaluation providers: {assignment_stats[2]:,}")
        print(f"Benchmark groups: {benchmark_stats[0]:,}")
        print(f"Minimum peer count: {benchmark_stats[1]}")
        print(f"Invalid quartile groups: {benchmark_stats[2]}")
        print(f"Groups with missing quartiles: {benchmark_stats[3]}")

        if benchmark_stats[2] or benchmark_stats[3]:
            raise RuntimeError("Benchmark validation failed.")

    finally:
        con.close()


if __name__ == "__main__":
    main()
