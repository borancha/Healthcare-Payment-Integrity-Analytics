
from pathlib import Path

import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(
    page_title="Healthcare Payment Integrity Analytics",
    page_icon="",
    layout="wide"
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = (
    PROJECT_ROOT
    / "data"
    / "analytics"
    / "rebuild_v1"
    / "evaluation_scored_2024.parquet"
)

st.title("Healthcare Payment Integrity & Provider Anomaly Analytics")
st.caption(
    "2024 Medicare provider/service data | Independent peer benchmarks | "
    "Analytical review support, not a fraud determination"
)

st.subheader("2024 Evaluation Overview")

overview_sql = f"""
SELECT
    COUNT(*) AS total_records,
    COUNT(DISTINCT Rndrng_NPI) AS distinct_providers,
    COUNT(*) FILTER (
        WHERE independent_payment_status = 'within_range'
    ) AS within_range,
    COUNT(*) FILTER (
        WHERE independent_payment_status = 'low_payment_outlier'
    ) AS low_outliers,
    COUNT(*) FILTER (
        WHERE independent_payment_status = 'high_payment_outlier'
    ) AS high_outliers,
    COUNT(*) FILTER (
        WHERE independent_payment_status =
            'not_scored_no_reference_benchmark'
    ) AS no_benchmark
FROM read_parquet('{DATA_FILE.as_posix()}')
"""

overview = duckdb.connect().execute(overview_sql).fetchdf().iloc[0]

o1, o2, o3 = st.columns(3)
o1.metric("Evaluation records", f"{int(overview['total_records']):,}")
o2.metric("Distinct providers", f"{int(overview['distinct_providers']):,}")
o3.metric(
    "Within peer range",
    f"{int(overview['within_range']):,}",
    f"{overview['within_range'] / overview['total_records']:.2%} of records"
)

o4, o5, o6 = st.columns(3)
o4.metric("High-payment candidates", f"{int(overview['high_outliers']):,}")
o5.metric("Low-payment candidates", f"{int(overview['low_outliers']):,}")
o6.metric("No reference benchmark", f"{int(overview['no_benchmark']):,}")

st.caption(
    "Overview figures cover the full evaluation dataset and do not change "
    "with sidebar filters. Outliers are review candidates, not confirmed fraud."
)
st.divider()

if not DATA_FILE.exists():
    st.error(f"Evaluation data file not found: {DATA_FILE}")
    st.stop()

@st.cache_resource
def get_connection(file_path):
    return duckdb.connect()

con = get_connection(str(DATA_FILE))
parquet_path = DATA_FILE.as_posix().replace("'", "''")
source = f"read_parquet('{parquet_path}')"

@st.cache_data
def get_filter_values(file_path):
    connection = duckdb.connect()
    path = str(file_path).replace("\\", "/").replace("'", "''")
    source_table = f"read_parquet('{path}')"

    states = connection.execute(
        f"SELECT DISTINCT Rndrng_Prvdr_State_Abrvtn "
        f"FROM {source_table} "
        f"WHERE Rndrng_Prvdr_State_Abrvtn IS NOT NULL "
        f"ORDER BY 1"
    ).fetchdf()["Rndrng_Prvdr_State_Abrvtn"].tolist()

    provider_types = connection.execute(
        f"SELECT DISTINCT Rndrng_Prvdr_Type "
        f"FROM {source_table} "
        f"WHERE Rndrng_Prvdr_Type IS NOT NULL "
        f"ORDER BY 1"
    ).fetchdf()["Rndrng_Prvdr_Type"].tolist()

    places = connection.execute(
        f"SELECT DISTINCT Place_Of_Srvc "
        f"FROM {source_table} "
        f"WHERE Place_Of_Srvc IS NOT NULL "
        f"ORDER BY 1"
    ).fetchdf()["Place_Of_Srvc"].tolist()

    connection.close()
    return states, provider_types, places

states, provider_types, places = get_filter_values(str(DATA_FILE))

st.sidebar.header("Review filters")

selected_statuses = st.sidebar.multiselect(
    "Payment status",
    [
        "high_payment_outlier",
        "low_payment_outlier",
        "within_range",
        "not_scored_no_reference_benchmark",
    ],
    default=["high_payment_outlier", "low_payment_outlier"],
)
selected_states = st.sidebar.multiselect("Provider state", states)
selected_types = st.sidebar.multiselect("Provider type", provider_types)
selected_places = st.sidebar.multiselect("Place of service", places)
min_peers = st.sidebar.number_input(
    "Minimum reference peer count",
    min_value=0,
    max_value=100000,
    value=30,
    step=10
)

conditions = [
    "("
    "independent_payment_status = 'not_scored_no_reference_benchmark' "
    "OR reference_peer_count >= ?"
    ")"
]
params = [int(min_peers)]

if selected_statuses:
    conditions.append("independent_payment_status IN (" +
                      ",".join(["?"] * len(selected_statuses)) + ")")
    params.extend(selected_statuses)
else:
    conditions.append("1 = 0")

for column, values in [
    ("Rndrng_Prvdr_State_Abrvtn", selected_states),
    ("Rndrng_Prvdr_Type", selected_types),
    ("Place_Of_Srvc", selected_places),
]:
    if values:
        conditions.append(column + " IN (" + ",".join(["?"] * len(values)) + ")")
        params.extend(values)

where_clause = " AND ".join(conditions)

summary_sql = f"""
SELECT
    COUNT(*) AS records,
    COUNT(DISTINCT Rndrng_NPI) AS providers,
    SUM(CASE WHEN independent_payment_status = 'high_payment_outlier'
             THEN 1 ELSE 0 END) AS high_outliers,
    SUM(CASE WHEN independent_payment_status = 'low_payment_outlier'
             THEN 1 ELSE 0 END) AS low_outliers,
    SUM(CASE WHEN independent_payment_status = 'within_range'
             THEN 1 ELSE 0 END) AS within_range
FROM {source}
WHERE {where_clause}
"""

summary = con.execute(summary_sql, params).fetchdf().iloc[0]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Records in selection", f"{int(summary['records']):,}")
c2.metric("Distinct providers", f"{int(summary['providers']):,}")
c3.metric("High-payment candidates", f"{int(summary['high_outliers']):,}")
c4.metric("Low-payment candidates", f"{int(summary['low_outliers']):,}")

st.divider()
left, right = st.columns(2)

state_sql = f"""
SELECT
    Rndrng_Prvdr_State_Abrvtn AS state,
    COUNT(*) AS records,
    SUM(CASE WHEN independent_payment_status = 'high_payment_outlier'
             THEN 1 ELSE 0 END) AS high_outliers,
    SUM(CASE WHEN independent_payment_status = 'low_payment_outlier'
             THEN 1 ELSE 0 END) AS low_outliers
FROM {source}
WHERE {where_clause}
GROUP BY 1
ORDER BY high_outliers DESC
LIMIT 20
"""
state_df = con.execute(state_sql, params).fetchdf()

with left:
    st.subheader("States with the most high-payment candidates")
    if not state_df.empty:
        fig = px.bar(
            state_df.sort_values("high_outliers"),
            x="high_outliers",
            y="state",
            orientation="h",
            labels={"high_outliers": "Candidate records", "state": "State"}
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No records match the current filters.")

type_sql = f"""
SELECT
    Rndrng_Prvdr_Type AS provider_type,
    COUNT(*) AS records,
    SUM(CASE WHEN independent_payment_status = 'high_payment_outlier'
             THEN 1 ELSE 0 END) AS high_outliers,
    SUM(CASE WHEN independent_payment_status = 'low_payment_outlier'
             THEN 1 ELSE 0 END) AS low_outliers
FROM {source}
WHERE {where_clause}
GROUP BY 1
ORDER BY high_outliers DESC
LIMIT 15
"""
type_df = con.execute(type_sql, params).fetchdf()

with right:
    st.subheader("Provider types with the most high-payment candidates")
    if not type_df.empty:
        fig = px.bar(
            type_df.sort_values("high_outliers"),
            x="high_outliers",
            y="provider_type",
            orientation="h",
            labels={
                "high_outliers": "Candidate records",
                "provider_type": "Provider type"
            }
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No records match the current filters.")

st.divider()
st.subheader("Payment review worklist")
st.caption(
    "Amounts are average Medicare payments per provider/service record. "
    "They are not total paid amounts or estimated savings."
)


candidate_sql = f"""
WITH candidates AS (
    SELECT
        Rndrng_NPI AS provider_npi,
        Rndrng_Prvdr_State_Abrvtn AS state,
        Rndrng_Prvdr_Type AS provider_type,
        HCPCS_Cd AS procedure_code,
        HCPCS_Desc AS procedure_description,
        Place_Of_Srvc AS place_of_service,
        independent_payment_status AS payment_status,
        ROUND(Avg_Mdcr_Pymt_Amt, 2) AS avg_medicare_payment,
        ROUND(payment_median, 2) AS peer_median,
        ROUND(payment_p75, 2) AS peer_p75,
        ROUND(upper_payment_bound, 2) AS upper_review_bound,
        ROUND(lower_payment_bound, 2) AS lower_review_bound,
        reference_peer_count AS reference_peers
    FROM {source}
    WHERE {where_clause}
      AND independent_payment_status IN (
          'high_payment_outlier', 'low_payment_outlier'
      )
),
distances AS (
    SELECT *,
        CASE
            WHEN payment_status = 'high_payment_outlier'
                THEN avg_medicare_payment - upper_review_bound
            WHEN payment_status = 'low_payment_outlier'
                THEN lower_review_bound - avg_medicare_payment
        END AS distance_outside_bound,
        100.0 * (avg_medicare_payment - peer_median)
            / NULLIF(ABS(peer_median), 0)
            AS deviation_pct_of_peer_median
    FROM candidates
),
prioritized AS (
    SELECT *,
        CASE
            WHEN distance_outside_bound >= 100
                 AND reference_peers >= 200
                THEN 'Priority 1'
            WHEN distance_outside_bound >= 25
                 AND reference_peers >= 50
                THEN 'Priority 2'
            WHEN reference_peers < 50
                THEN 'Review benchmark support'
            ELSE 'Standard'
        END AS review_priority,
        CASE
            WHEN reference_peers < 50 THEN 'Limited (30–49)'
            WHEN reference_peers < 200 THEN 'Moderate (50–199)'
            ELSE 'Larger (200+)'
        END AS peer_support,
        CASE
            WHEN payment_status = 'high_payment_outlier'
                THEN 'Payment exceeds upper peer IQR bound'
            WHEN payment_status = 'low_payment_outlier'
                THEN 'Payment falls below lower peer IQR bound'
            ELSE 'No payment outlier identified'
        END AS review_reason
    FROM distances
)
SELECT *
FROM prioritized
ORDER BY
    CASE review_priority
        WHEN 'Priority 1' THEN 0
        WHEN 'Priority 2' THEN 1
        WHEN 'Standard' THEN 2
        WHEN 'Review benchmark support' THEN 3
        ELSE 4
    END,
    distance_outside_bound DESC NULLS LAST
LIMIT 500
"""

worklist = con.execute(candidate_sql, params).fetchdf()

def explain_payment_deviation(row):
    status = row["payment_status"]
    pct = row["deviation_pct_of_peer_median"]
    distance = row["distance_outside_bound"]
    peers = row["reference_peers"]

    if status == "high_payment_outlier":
        direction = "above"
    elif status == "low_payment_outlier":
        direction = "below"
    else:
        return "No peer-IQR payment outlier identified."

    pct_text = (
        f"{abs(pct):.1f}%"
        if pd.notna(pct)
        else "an unknown percentage"
    )
    distance_text = (
        f"${distance:,.2f}"
        if pd.notna(distance)
        else "an unknown amount"
    )
    peer_text = (
        f"{int(peers):,} reference peers"
        if pd.notna(peers)
        else "an unavailable number of reference peers"
    )

    return (
        f"Payment is {pct_text} {direction} peer median; "
        f"{distance_text} beyond the review bound; "
        f"benchmark uses {peer_text}."
    )


worklist["deviation_explanation"] = worklist.apply(
    explain_payment_deviation, axis=1
)


if not worklist.empty:
    priority_rank = {
        "Priority 1": 0,
        "Priority 2": 1,
        "Standard": 2,
        "Review benchmark support": 3
    }

    ranks = worklist["review_priority"].map(priority_rank)

    st.caption(
        f"Worklist validation: {len(worklist):,} rows displayed | "
        f"Priority order valid: {ranks.is_monotonic_increasing}"
    )

if worklist.empty:
    st.info("No payment review candidates match the current filters.")
else:
    st.dataframe(
    worklist[
        [
            "provider_npi",
            "state",
            "provider_type",
            "procedure_code",
            "procedure_description",
            "payment_status",
            "review_reason",
            "deviation_explanation",
            "avg_medicare_payment",
            "peer_median",
            "upper_review_bound",
            "distance_outside_bound",
            "deviation_pct_of_peer_median",
            "reference_peers",
            "peer_support",
            "review_priority",
        ]
    ],
    use_container_width=True,
    hide_index=True,
)
    st.download_button(
        "Download displayed worklist as CSV",
        data=worklist.to_csv(index=False).encode("utf-8"),
        file_name="payment_review_worklist.csv",
        mime="text/csv"
    )

st.warning(
    "Interpretation guardrail: an outlier is a statistical signal for human "
    "review, not proof of fraud, improper payment, or recoverable savings. "
    "Differences may reflect legitimate clinical, geographic, coding, or "
    "service-mix factors."
)


st.divider()
st.divider()
st.header("ML Anomaly Screening")
st.caption(
    "Exploratory results from a 20,000-record evaluation sample. "
    "ML anomaly flags are screening signals, not confirmed fraud."
)

ML_SAMPLE_FILE = (
    PROJECT_ROOT
    / "data"
    / "analytics"
    / "ml_training_sample_seed42_v2"
    / "ml_comparison_scores_sample.parquet"
)
PAYMENT_SCORES_FILE = (
    PROJECT_ROOT
    / "data"
    / "analytics"
    / "provider_payment_anomaly_scores_2024_v2.parquet"
)

if not ML_SAMPLE_FILE.exists() or not PAYMENT_SCORES_FILE.exists():
    st.warning(
        "ML screening data files were not found. "
        "The existing payment review dashboard remains available."
    )
else:
    ml_path = ML_SAMPLE_FILE.as_posix().replace("'", "''")
    payment_path = PAYMENT_SCORES_FILE.as_posix().replace("'", "''")

    ml_screening_sql = f"""
    SELECT
        m.provider_id AS provider_npi,
        b.Rndrng_Prvdr_State_Abrvtn AS state,
        b.Rndrng_Prvdr_Type AS provider_type,
        m.hcpcs_code AS procedure_code,
        b.HCPCS_Desc AS procedure_description,
        m.place_of_service,
        m.independent_payment_status AS payment_status,
        ROUND(b.Avg_Mdcr_Pymt_Amt, 2) AS avg_medicare_payment,
        ROUND(b.payment_p25, 2) AS peer_p25,
        ROUND(b.payment_p75, 2) AS peer_p75,
        ROUND(b.lower_payment_bound, 2) AS lower_review_bound,
        ROUND(b.upper_payment_bound, 2) AS upper_review_bound,
        b.benchmark_peer_count AS reference_peers,
        m.isolation_forest_anomaly_score,
        m.isolation_forest_flag,
        m.lof_anomaly_score,
        m.lof_flag,
        m.iqr_outlier_flag,
        CASE
            WHEN m.isolation_forest_flag AND m.lof_flag
                THEN 'Both ML methods'
            WHEN m.isolation_forest_flag
                THEN 'Isolation Forest only'
            WHEN m.lof_flag
                THEN 'LOF only'
            ELSE 'Neither ML method'
        END AS ml_method_agreement,
        CASE
            WHEN m.isolation_forest_flag AND m.lof_flag
                THEN 'Review: both ML methods flagged'
            WHEN m.isolation_forest_flag OR m.lof_flag
                THEN 'Review: one ML method flagged'
            ELSE 'No ML flag at configured threshold'
        END AS ml_review_reason
    FROM read_parquet('{ml_path}') m
    INNER JOIN read_parquet('{payment_path}') b
        ON m.provider_id = b.Rndrng_NPI
       AND m.hcpcs_code = b.HCPCS_Cd
       AND m.place_of_service = b.Place_Of_Srvc
    """

    ml_screening = con.execute(ml_screening_sql).fetchdf()

    ml_filter = st.radio(
        "ML review filter",
        [
            "Any ML result",
            "Isolation Forest flagged",
            "LOF flagged",
            "Both ML methods flagged",
            "IQR payment outlier",
            "ML and IQR both flagged",
        ],
        horizontal=True,
        key="ml_screening_filter",
    )

    if ml_filter == "Isolation Forest flagged":
        ml_view = ml_screening[
            ml_screening["isolation_forest_flag"]
        ].copy()
    elif ml_filter == "LOF flagged":
        ml_view = ml_screening[ml_screening["lof_flag"]].copy()
    elif ml_filter == "Both ML methods flagged":
        ml_view = ml_screening[
            ml_screening["isolation_forest_flag"]
            & ml_screening["lof_flag"]
        ].copy()
    elif ml_filter == "IQR payment outlier":
        ml_view = ml_screening[
            ml_screening["iqr_outlier_flag"]
        ].copy()
    elif ml_filter == "ML and IQR both flagged":
        ml_view = ml_screening[
            (
                ml_screening["isolation_forest_flag"]
                | ml_screening["lof_flag"]
            )
            & ml_screening["iqr_outlier_flag"]
        ].copy()
    else:
        ml_view = ml_screening.copy()

    total_ml_rows = len(ml_screening)
    iforest_count = int(ml_screening["isolation_forest_flag"].sum())
    lof_count = int(ml_screening["lof_flag"].sum())
    iqr_count = int(ml_screening["iqr_outlier_flag"].sum())
    both_ml_count = int(
        (
            ml_screening["isolation_forest_flag"]
            & ml_screening["lof_flag"]
        ).sum()
    )
    ml_and_iqr_count = int(
        (
            (
                ml_screening["isolation_forest_flag"]
                | ml_screening["lof_flag"]
            )
            & ml_screening["iqr_outlier_flag"]
        ).sum()
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("Matched sample records", f"{total_ml_rows:,}")
    c2.metric("Isolation Forest flagged", f"{iforest_count:,}")
    c3.metric("LOF flagged", f"{lof_count:,}")

    c4, c5, c6 = st.columns(3)
    c4.metric("Both ML methods flagged", f"{both_ml_count:,}")
    c5.metric("IQR payment outliers", f"{iqr_count:,}")
    c6.metric("ML and IQR overlap", f"{ml_and_iqr_count:,}")

    st.caption(
        "An ML flag means a record was identified as unusual by that model. "
        "An IQR flag means its payment falls outside the peer payment bounds. "
        "The methods measure different signals."
    )

    display_columns = [
        "provider_npi",
        "state",
        "provider_type",
        "procedure_code",
        "procedure_description",
        "place_of_service",
        "payment_status",
        "avg_medicare_payment",
        "lower_review_bound",
        "upper_review_bound",
        "reference_peers",
        "isolation_forest_anomaly_score",
        "isolation_forest_flag",
        "lof_anomaly_score",
        "lof_flag",
        "iqr_outlier_flag",
        "ml_method_agreement",
        "ml_review_reason",
    ]

    st.caption(
        f"Rows shown: {len(ml_view):,} of {total_ml_rows:,} sampled records."
    )
    st.dataframe(
        ml_view[display_columns],
        use_container_width=True,
        hide_index=True,
    )

    st.download_button(
        "Download ML screening results as CSV",
        data=ml_view[display_columns].to_csv(index=False).encode("utf-8"),
        file_name="ml_anomaly_screening_sample.csv",
        mime="text/csv",
        key="download_ml_screening",
    )

    st.warning(
        "This is a bounded sample, not full-population ML scoring. "
        "Anomaly scores and flags do not establish fraud, improper payment, "
        "or recoverable savings. Human review and supporting evidence are required."
    )
st.header("Provider-Level Payment Review")

st.write(
    "Search a provider NPI to examine payment records against the "
    "corresponding provider-type, procedure-code, and place-of-service "
    "reference benchmarks."
)

provider_npi_input = st.text_input(
    "Enter 10-digit Provider NPI",
    max_chars=10,
    placeholder="e.g., 1104850312"
)

if provider_npi_input:
    if not provider_npi_input.isdigit() or len(provider_npi_input) != 10:
        st.error("Enter a valid 10-digit numeric NPI.")
    else:
        provider_sql = f"""
        SELECT
            Rndrng_NPI AS provider_npi,
            Rndrng_Prvdr_State_Abrvtn AS state,
            Rndrng_Prvdr_Type AS provider_type,
            HCPCS_Cd AS procedure_code,
            HCPCS_Desc AS procedure_description,
            Place_Of_Srvc AS place_of_service,
            independent_payment_status AS payment_status,
            Avg_Mdcr_Pymt_Amt AS avg_payment,
            payment_median AS peer_median,
            payment_p25 AS peer_p25,
            payment_p75 AS peer_p75,
            upper_payment_bound AS upper_review_bound,
            lower_payment_bound AS lower_review_bound,
            reference_peer_count AS reference_peers
        FROM {source}
        WHERE Rndrng_NPI = ?
        ORDER BY
            CASE
                WHEN independent_payment_status = 'high_payment_outlier'
                    THEN 0
                WHEN independent_payment_status = 'low_payment_outlier'
                    THEN 1
                ELSE 2
            END,
            Avg_Mdcr_Pymt_Amt DESC
        LIMIT 500
        """

        provider_df = con.execute(
            provider_sql, [int(provider_npi_input)]
        ).fetchdf()

        if provider_df.empty:
            st.info(
                "No matching provider records were found in the "
                "independent evaluation dataset."
            )
        else:
            st.subheader(f"Provider NPI: {provider_npi_input}")

            p1, p2, p3 = st.columns(3)
            p1.metric("Records found", f"{len(provider_df):,}")
            p2.metric(
                "High-payment candidates",
                f"{(provider_df['payment_status'] == 'high_payment_outlier').sum():,}"
            )
            p3.metric(
                "Low-payment candidates",
                f"{(provider_df['payment_status'] == 'low_payment_outlier').sum():,}"
            )

            st.caption(
                "The table is limited to 500 records. Metrics above describe "
                "the displayed result set, not necessarily every record "
                "for the provider."
            )

            st.subheader("Payment Evidence Summary")

            high_count = int(
                (provider_df["payment_status"] == "high_payment_outlier").sum()
            )
            low_count = int(
                (provider_df["payment_status"] == "low_payment_outlier").sum()
            )
            reviewed_count = high_count + low_count

            st.write(
                f"Among the {len(provider_df):,} displayed provider-procedure "
                f"records, {reviewed_count:,} were identified as payment "
                f"outlier candidates by the peer-IQR screening rule."
            )

            if reviewed_count:
                st.write(
                    f"- **High-payment candidates:** {high_count:,}\n"
                    f"- **Low-payment candidates:** {low_count:,}"
                )
            else:
                st.write(
                    "No high- or low-payment outlier candidates were "
                    "identified in these displayed records."
                )

            st.caption(
                "This summary describes the displayed provider records, "
                "which are limited to 500. Outlier candidates require "
                "further review and are not findings of fraud or improper payment."
            )


            st.subheader("Payment versus peer benchmarks")

            chart_df = provider_df.dropna(
                subset=["avg_payment", "peer_median", "peer_p75"]
            ).copy()

            if not chart_df.empty:
                chart_df["record_label"] = (
                    chart_df["procedure_code"].astype(str)
                    + " | "
                    + chart_df["place_of_service"].astype(str)
                )

                chart_df = chart_df.head(50)

                chart_long = chart_df.melt(
                    id_vars=["record_label"],
                    value_vars=[
                        "avg_payment",
                        "peer_median",
                        "peer_p75",
                        "upper_review_bound"
                    ],
                    var_name="measure",
                    value_name="amount"
                ).dropna(subset=["amount"])

                fig = px.bar(
                    chart_long,
                    x="record_label",
                    y="amount",
                    color="measure",
                    barmode="group",
                    title="Average payment and peer reference values",
                    labels={
                        "record_label": "Procedure code | Place of service",
                        "amount": "Average Medicare payment ($)",
                        "measure": "Measure"
                    }
                )
                fig.update_layout(xaxis_tickangle=-45)
                st.plotly_chart(fig, use_container_width=True)

            st.subheader("ML Investigation Details")

            if "ml_screening" in globals():
                provider_ml_df = ml_screening[
                    ml_screening["provider_npi"].astype(str)
                    == provider_npi_input
                ].copy()

                if provider_ml_df.empty:
                    st.info(
                        "This provider has no matching records in the "
                        "current 20,000-record ML screening sample. "
                        "This does not mean the provider has no anomalies."
                    )
                else:
                    ml1, ml2, ml3 = st.columns(3)

                    ml1.metric(
                        "Isolation Forest flags",
                        int(provider_ml_df["isolation_forest_flag"].sum())
                    )
                    ml2.metric(
                        "LOF flags",
                        int(provider_ml_df["lof_flag"].sum())
                    )
                    ml3.metric(
                        "IQR payment outliers",
                        int(provider_ml_df["iqr_outlier_flag"].sum())
                    )

                    investigation_columns = [
                        "procedure_code",
                        "procedure_description",
                        "place_of_service",
                        "payment_status",
                        "avg_medicare_payment",
                        "reference_peers",
                        "isolation_forest_anomaly_score",
                        "isolation_forest_flag",
                        "lof_anomaly_score",
                        "lof_flag",
                        "iqr_outlier_flag",
                        "ml_method_agreement",
                        "ml_review_reason",
                    ]

                    st.dataframe(
                        provider_ml_df[investigation_columns],
                        use_container_width=True,
                        hide_index=True,
                    )

                    st.caption(
                        "ML results are available only for records included "
                        "in the bounded screening sample. A missing ML result "
                        "is not evidence of normal behavior."
                    )
            else:
                st.info(
                    "ML screening files are unavailable. The standard "
                    "provider payment review remains available."
                )


            st.subheader("Provider payment records")
            display_df = provider_df.copy()

            money_columns = [
                "avg_payment", "peer_median", "peer_p25", "peer_p75",
                "upper_review_bound", "lower_review_bound"
            ]
            for col in money_columns:
                display_df[col] = display_df[col].round(2)

            st.dataframe(
                display_df,
                use_container_width=True,
                hide_index=True
            )

            st.download_button(
                "Download provider records as CSV",
                data=display_df.to_csv(index=False).encode("utf-8"),
                file_name=f"provider_{provider_npi_input}_review.csv",
                mime="text/csv"
            )

            st.info(
                "A payment outside the peer-based range is a review signal, "
                "not proof of fraud or an improper payment. Review clinical, "
                "coding, geographic, and service-specific context before "
                "drawing conclusions."
            )


