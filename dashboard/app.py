
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
    / "evaluation"
    / "independent_evaluation_scores_2024.parquet"
)

st.title("Healthcare Payment Integrity & Provider Anomaly Analytics")
st.caption(
    "2024 Medicare provider/service data | Independent peer benchmarks | "
    "Analytical review support, not a fraud determination"
)

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
    ["high_payment_outlier", "low_payment_outlier", "within_range",
     "not_scored_insufficient_reference_peers"],
    default=["high_payment_outlier", "low_payment_outlier"]
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

conditions = ["reference_peer_count >= ?"]
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
        END AS peer_support
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


