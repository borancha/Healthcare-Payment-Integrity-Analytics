from pathlib import Path
import duckdb

project_root = Path(__file__).resolve().parent

original_file = (
    project_root
    / "data/analytics/pipeline_test/"
    / "independent_evaluation_scored_by_script_2024.parquet"
)

reproduced_file = (
    project_root
    / "data/analytics/pipeline_test/"
    / "reproduced_scoring_check.parquet"
)

if not original_file.exists():
    raise FileNotFoundError(f"Original output not found: {original_file}")

if not reproduced_file.exists():
    raise FileNotFoundError(f"Reproduced output not found: {reproduced_file}")

con = duckdb.connect()

try:
    original_count = con.execute(
        "SELECT COUNT(*) FROM read_parquet(?)",
        [str(original_file)],
    ).fetchone()[0]

    reproduced_count = con.execute(
        "SELECT COUNT(*) FROM read_parquet(?)",
        [str(reproduced_file)],
    ).fetchone()[0]

    original_only = con.execute(
        """
        SELECT COUNT(*)
        FROM (
            SELECT * FROM read_parquet(?)
            EXCEPT ALL
            SELECT * FROM read_parquet(?)
        ) AS differences
        """,
        [str(original_file), str(reproduced_file)],
    ).fetchone()[0]

    reproduced_only = con.execute(
        """
        SELECT COUNT(*)
        FROM (
            SELECT * FROM read_parquet(?)
            EXCEPT ALL
            SELECT * FROM read_parquet(?)
        ) AS differences
        """,
        [str(reproduced_file), str(original_file)],
    ).fetchone()[0]

    print(f"Original records:             {original_count:,}")
    print(f"Reproduced records:           {reproduced_count:,}")
    print(f"Rows only in original:        {original_only:,}")
    print(f"Rows only in reproduced:      {reproduced_only:,}")

    if (
        original_count == reproduced_count
        and original_only == 0
        and reproduced_only == 0
    ):
        print("\nRESULT: PASS — outputs match exactly.")
    else:
        print("\nRESULT: FAIL — outputs differ.")

finally:
    con.close()