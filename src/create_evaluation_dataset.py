
"""Create a held-out provider evaluation dataset from the CMS source.

Only providers assigned to the evaluation split are included.
This writes a new Parquet file and does not modify the source data.
"""

import argparse
from pathlib import Path

import duckdb


def sql_literal(path: Path) -> str:
    """Safely quote a filesystem path for DuckDB SQL."""
    return "'" + str(path.resolve()).replace("'", "''") + "'"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract held-out evaluation providers."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(
            "data/processed/cms_medicare_provider_service_2024.csv"
        ),
    )
    parser.add_argument(
        "--assignments",
        type=Path,
        default=Path(
            "data/analytics/rebuild_v1/provider_split_assignments_2024.parquet"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/analytics/rebuild_v1/evaluation_input_2024.parquet"
        ),
    )
    args = parser.parse_args()

    input_path = args.input.resolve()
    assignments_path = args.assignments.resolve()
    output_path = args.output.resolve()

    if not input_path.exists():
        raise FileNotFoundError(f"CMS input not found: {input_path}")
    if not assignments_path.exists():
        raise FileNotFoundError(
            f"Provider assignments not found: {assignments_path}"
        )
    if input_path == output_path or assignments_path == output_path:
        raise ValueError("Output path must differ from input paths.")
    if output_path.exists():
        raise FileExistsError(
            f"Output already exists: {output_path}. "
            "Choose a new output path to avoid overwriting it."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    source = f"read_csv_auto({sql_literal(input_path)}, header=true)"
    assignments = f"read_parquet({sql_literal(assignments_path)})"
    con = duckdb.connect(database=":memory:")

    try:
        print("Extracting held-out evaluation providers...")
        con.execute(
            f"""
            COPY (
                SELECT src.*
                FROM {source} AS src
                INNER JOIN {assignments} AS split
                    ON src.Rndrng_NPI = split.Rndrng_NPI
                WHERE split.split = 'evaluation'
            )
            TO {sql_literal(output_path)}
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )

        result = con.execute(
            """
            SELECT
                COUNT(*) AS evaluation_records,
                COUNT(DISTINCT Rndrng_NPI) AS distinct_providers,
                COUNT(*) FILTER (
                    WHERE Avg_Mdcr_Pymt_Amt IS NULL
                ) AS missing_payment_records
            FROM read_parquet(?)
            """,
            [str(output_path)],
        ).fetchone()

        print("\nEvaluation dataset created.")
        print(f"Output: {output_path}")
        print(f"Evaluation records: {result[0]:,}")
        print(f"Distinct providers: {result[1]:,}")
        print(f"Records with missing average payment: {result[2]:,}")

    finally:
        con.close()


if __name__ == "__main__":
    main()
