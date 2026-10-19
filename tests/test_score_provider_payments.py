import unittest

import duckdb


class PaymentClassificationTests(unittest.TestCase):
    """Test the statistical classification rules used by the scorer."""

    def setUp(self):
        self.con = duckdb.connect(database=":memory:")

        self.con.execute("""
            CREATE TABLE test_records (
                payment DOUBLE,
                p25 DOUBLE,
                p75 DOUBLE,
                has_benchmark BOOLEAN
            )
        """)

        # P25 = 10, P75 = 20, IQR = 10.
        # Lower bound = -5; upper bound = 35.
        self.con.execute("""
            INSERT INTO test_records VALUES
                (40, 10, 20, TRUE),
                (-6, 10, 20, TRUE),
                (15, 10, 20, TRUE),
                (NULL, 10, 20, TRUE),
                (15, NULL, NULL, FALSE)
        """)

    def tearDown(self):
        self.con.close()

    def test_classification_rules(self):
        result = self.con.execute("""
            SELECT
                CASE
                    WHEN NOT has_benchmark
                        THEN 'not_scored_no_reference_benchmark'
                    WHEN payment IS NULL
                        THEN 'not_scored_missing_payment'
                    WHEN payment < (p25 - 1.5 * (p75 - p25))
                        THEN 'low_payment_outlier'
                    WHEN payment > (p75 + 1.5 * (p75 - p25))
                        THEN 'high_payment_outlier'
                    ELSE 'within_range'
                END AS status
            FROM test_records
            ORDER BY rowid
        """).fetchall()

        statuses = [row[0] for row in result]

        self.assertEqual(statuses, [
            "high_payment_outlier",
            "low_payment_outlier",
            "within_range",
            "not_scored_missing_payment",
            "not_scored_no_reference_benchmark",
        ])

    def test_boundary_values_are_within_range(self):
        result = self.con.execute("""
            SELECT
                CASE
                    WHEN payment < (p25 - 1.5 * (p75 - p25))
                        THEN 'low'
                    WHEN payment > (p75 + 1.5 * (p75 - p25))
                        THEN 'high'
                    ELSE 'within'
                END
            FROM (
                SELECT 35.0 AS payment, 10.0 AS p25, 20.0 AS p75
                UNION ALL
                SELECT -5.0, 10.0, 20.0
            )
        """).fetchall()

        self.assertEqual([row[0] for row in result], [
            "within",
            "within",
        ])


if __name__ == "__main__":
    unittest.main()
