"""Score Medicare provider-service payment records against fixed peer benchmarks.

This script identifies statistical payment outliers for human review.
It does not establish fraud, improper payment, or recoverable savings.
"""

from pathlib import Path
import argparse

import duckdb


def sql_literal(value: Path) -> str:
    """Return a safely escaped SQL string literal for a filesystem path."""
    return "'" + str(value).replace("\\\\", "/").replace("'", "''") + "'"


PEER_KEYS = [
    "Rndrng_Prvdr_Type",
    "HCPCS_Cd",
    "Place_Of_Srvc",
]

REQUIRED_INPUT_COLUMNS = [
    "Rndrng_NPI",
    "Rndrng_Prvdr_State_Abrvtn",
    "Rndrng_Prvdr_Type",
    "HCPCS_Cd",
    "HCPCS_Desc",
    "Place_Of_Srvc",
    "Avg_Mdcr_Pymt_Amt",
]


def score_file(input_path: Path, benchmark_path: Path,
               output_path: Path) -> None:
    """Score an input Parquet or CSV file and write scored Parquet output."""

    input_path = input_path.resolve()
    benchmark_path = benchmark_path.resolve()
    output_path = output_path.resolve()

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    if not benchmark_path.exists():
        raise FileNotFoundError(
            f"Benchmark file not found: {benchmark_path}"
        )
    if input_path == output_path or benchmark_path == output_path:
        raise ValueError("Output path must differ from input and benchmark.")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(database=":memory:")

    try:
        input_sql = (
            "read_parquet(?)"
            if input_path.suffix.lower() == ".parquet"
            else "read_csv_auto(?, header=true)"
        )

        # Validate the incoming schema before scoring.
        input_columns = {
            row[0]
            for row in con.execute(
                f"DESCRIBE SELECT * FROM {input_sql}",
                [str(input_path)],
            ).fetchall()
        }

        missing = sorted(set(REQUIRED_INPUT_COLUMNS) - input_columns)
        if missing:
            raise ValueError(
                f"Input is missing required columns: {missing}"
            )

        benchmark_columns = {
            row[0]
            for row in con.execute(
                "DESCRIBE SELECT * FROM read_parquet(?)",
                [str(benchmark_path)],
            ).fetchall()
        }

        required_benchmark_columns = set(PEER_KEYS) | {
            "reference_peer_count",
            "payment_p25",
            "payment_median",
            "payment_p75",
        }

        missing_benchmarks = sorted(
            required_benchmark_columns - benchmark_columns
        )
        if missing_benchmarks:
            raise ValueError(
                f"Benchmark file is missing columns: {missing_benchmarks}"
            )

        # A 1.5-IQR rule is used for transparent, peer-relative screening.
        # A record without a matching benchmark is not scored.
        input_source = (
            f"read_parquet({sql_literal(input_path)})"
            if input_path.suffix.lower() == ".parquet"
            else f"read_csv_auto({sql_literal(input_path)}, header=true)"
        )
        benchmark_source = f"read_parquet({sql_literal(benchmark_path)})"
        output_target = sql_literal(output_path)

        con.execute(
            f"""
            COPY (
                SELECT
                    src.*,
                    b.reference_peer_count,
                    b.payment_p25,
                    b.payment_median,
                    b.payment_p75,
                    (b.payment_p75 - b.payment_p25) AS payment_iqr,
                    (b.payment_p25 - 1.5 *
                        (b.payment_p75 - b.payment_p25)
                    ) AS lower_payment_bound,
                    (b.payment_p75 + 1.5 *
                        (b.payment_p75 - b.payment_p25)
                    ) AS upper_payment_bound,
                    CASE
                        WHEN b.reference_peer_count IS NULL
                            THEN 'not_scored_no_reference_benchmark'
                        WHEN src.Avg_Mdcr_Pymt_Amt IS NULL
                            THEN 'not_scored_missing_payment'
                        WHEN src.Avg_Mdcr_Pymt_Amt <
                            (b.payment_p25 - 1.5 *
                                (b.payment_p75 - b.payment_p25))
                            THEN 'low_payment_outlier'
                        WHEN src.Avg_Mdcr_Pymt_Amt >
                            (b.payment_p75 + 1.5 *
                                (b.payment_p75 - b.payment_p25))
                            THEN 'high_payment_outlier'
                        ELSE 'within_range'
                    END AS independent_payment_status
                FROM {input_source} AS src
                LEFT JOIN {benchmark_source} AS b
                    ON src.Rndrng_Prvdr_Type = b.Rndrng_Prvdr_Type
                    AND src.HCPCS_Cd = b.HCPCS_Cd
                    AND src.Place_Of_Srvc = b.Place_Of_Srvc
            ) TO {output_target} (FORMAT PARQUET, COMPRESSION ZSTD)
            """,
            [],
        )

        row_count = con.execute(
            "SELECT COUNT(*) FROM read_parquet(?)",
            [str(output_path)],
        ).fetchone()[0]

        print(f"Scored records: {row_count:,}")
        print(f"Output file: {output_path}")

        print("\nStatus counts:")
        status_counts = con.execute(
            """
            SELECT
                independent_payment_status,
                COUNT(*) AS records
            FROM read_parquet(?)
            GROUP BY independent_payment_status
            ORDER BY records DESC
            """,
            [str(output_path)],
        ).df()
        print(status_counts.to_string(index=False))

    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score provider-service payment records."
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--benchmarks", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    score_file(args.input, args.benchmarks, args.output)


if __name__ == "__main__":
    main()
