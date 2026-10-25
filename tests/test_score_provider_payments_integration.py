import csv
import tempfile
import unittest
from pathlib import Path

import duckdb

from src.score_provider_payments import score_file


class ScoreFileIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.input_path = self.root / "input.csv"
        self.benchmark_path = self.root / "benchmarks.parquet"
        self.output_path = self.root / "scored.parquet"

        columns = [
            "Rndrng_NPI",
            "Rndrng_Prvdr_State_Abrvtn",
            "Rndrng_Prvdr_Type",
            "HCPCS_Cd",
            "HCPCS_Desc",
            "Place_Of_Srvc",
            "Avg_Mdcr_Pymt_Amt",
        ]

        records = [
            ["1001", "PA", "Family Practice", "99213", "Office visit", "O", "40"],
            ["1002", "PA", "Family Practice", "99213", "Office visit", "O", "-6"],
            ["1003", "PA", "Family Practice", "99213", "Office visit", "O", "15"],
            ["1004", "PA", "Family Practice", "99213", "Office visit", "O", ""],
            ["1005", "PA", "Family Practice", "99999", "Other service", "O", "15"],
        ]

        with self.input_path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(columns)
            writer.writerows(records)

        con = duckdb.connect()
        try:
            con.execute("""
                COPY (
                    SELECT
                        'Family Practice' AS Rndrng_Prvdr_Type,
                        '99213' AS HCPCS_Cd,
                        'O' AS Place_Of_Srvc,
                        40 AS reference_peer_count,
                        10.0 AS payment_p25,
                        15.0 AS payment_median,
                        20.0 AS payment_p75
                ) TO ? (FORMAT PARQUET)
            """, [str(self.benchmark_path)])
        finally:
            con.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_scores_real_input_and_preserves_rows(self):
        score_file(self.input_path, self.benchmark_path, self.output_path)

        con = duckdb.connect()
        try:
            rows = con.execute("""
                SELECT Rndrng_NPI, independent_payment_status
                FROM read_parquet(?)
                ORDER BY Rndrng_NPI
            """, [str(self.output_path)]).fetchall()
        finally:
            con.close()

        self.assertEqual(len(rows), 5)
        self.assertEqual(
            [status for _, status in rows],
            [
                "high_payment_outlier",
                "low_payment_outlier",
                "within_range",
                "not_scored_missing_payment",
                "not_scored_no_reference_benchmark",
            ],
        )

    def test_rejects_input_missing_required_columns(self):
        bad_input = self.root / "bad_input.csv"
        bad_input.write_text(
            "Rndrng_NPI\n1001\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "missing required columns"):
            score_file(
                bad_input,
                self.benchmark_path,
                self.root / "bad_output.parquet",
            )

    def test_rejects_output_path_equal_to_input(self):
        with self.assertRaisesRegex(ValueError, "Output path must differ"):
            score_file(
                self.input_path,
                self.benchmark_path,
                self.input_path,
            )

    def test_rejects_benchmark_missing_required_columns(self):
        invalid_benchmark = self.root / "invalid_benchmark.parquet"

        con = duckdb.connect()
        try:
            con.execute("""
                COPY (
                    SELECT 1 AS Rndrng_Prvdr_Type
                ) TO ? (FORMAT PARQUET)
            """, [str(invalid_benchmark)])
        finally:
            con.close()

        with self.assertRaisesRegex(
            ValueError, "Benchmark file is missing columns"
        ):
            score_file(
                self.input_path,
                invalid_benchmark,
                self.root / "invalid_benchmark_output.parquet",
            )

    def test_rejects_output_path_equal_to_benchmark(self):
        with self.assertRaisesRegex(ValueError, "Output path must differ"):
            score_file(
                self.input_path,
                self.benchmark_path,
                self.benchmark_path,
            )
