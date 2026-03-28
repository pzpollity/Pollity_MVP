"""
Jan-Sunwai Dashboard
---------------------
Streamlit app for the elected representative's office staff.
Displays incoming grievances, lets staff update status, and shows summary charts.

Run locally:
    streamlit run dashboard/app.py

Deploy: Streamlit Community Cloud (free tier)
"""

import os

import httpx
import pandas as pd
import plotly.express as px
import streamlit as st
from supabase import create_client

# ── Config ────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Jan-Sunwai | Pollity.in",
    page_icon="🏛️",
    layout="wide",
)

# Works on both Streamlit Cloud (st.secrets) and local (os.environ)
def _get(key, default=""):
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, default)

SUPABASE_URL = _get("SUPABASE_URL")
# Phase 1 demo: service role key bypasses RLS so dashboard can read grievances.
# Phase 2: switch back to SUPABASE_ANON_KEY + Supabase Auth session per user.
SUPABASE_KEY = _get("SUPABASE_SERVICE_ROLE_KEY") or _get("SUPABASE_ANON_KEY")
# For demo: hardcoded office_id — in Phase 2 this comes from Supabase Auth session
DEMO_OFFICE_ID = _get("DEMO_OFFICE_ID")
# Backend API URL for walk-in intake
BACKEND_URL = _get("BACKEND_URL", "https://pollitymvp-production.up.railway.app")

STATUS_ORDER = [
    "registered", "acknowledged", "assigned",
    "in_progress", "resolved", "verified", "closed"
]
URGENCY_COLORS = {
    "critical": "#e74c3c",
    "high":     "#e67e22",
    "medium":   "#3498db",
    "low":      "#2ecc71",
}


@st.cache_resource
def get_supabase():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def load_grievances(office_id: str) -> pd.DataFrame:
    db = get_supabase()
    resp = (
        db.table("grievances")
        .select("*")
        .eq("office_id", office_id)
        .order("filed_at", desc=True)
        .limit(500)
        .execute()
    )
    if not resp.data:
        return pd.DataFrame()
    df = pd.DataFrame(resp.data)
    df["filed_at"] = pd.to_datetime(df["filed_at"])
    df["status"] = pd.Categorical(df["status"], categories=STATUS_ORDER, ordered=True)
    return df


def update_status(grievance_uuid: str, new_status: str, assigned_to: str, next_action: str):
    db = get_supabase()
    from datetime import datetime, timezone
    patch = {
        "status": new_status,
        "assigned_to": assigned_to,
        "next_action": next_action,
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    if new_status == "closed":
        patch["closed_at"] = patch["updated_at"]
    db.table("grievances").update(patch).eq("id", grievance_uuid).execute()


# ── Layout ────────────────────────────────────────────────────────────────────
st.title("🏛️ Jan-Sunwai — Grievance Operations Dashboard")
st.caption("Pollity.in · Constituency Office Staff Portal")

if not DEMO_OFFICE_ID:
    st.error("DEMO_OFFICE_ID environment variable not set.")
    st.stop()

df = load_grievances(DEMO_OFFICE_ID)

if df.empty:
    st.info("No grievances registered yet. Send a WhatsApp message to get started.")
    st.stop()

# ── KPI Row ───────────────────────────────────────────────────────────────────
open_df = df[df["status"] != "closed"]
critical_count = (open_df["urgency"] == "critical").sum()
high_count = (open_df["urgency"] == "high").sum()
resolved_today = df[
    (df["status"].isin(["resolved", "verified", "closed"])) &
    (df["filed_at"].dt.date == pd.Timestamp.today().date())
].shape[0]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Grievances", len(df))
c2.metric("Open", len(open_df))
c3.metric("🔴 Critical", int(critical_count))
c4.metric("Resolved Today", resolved_today)

st.divider()

# ── Charts ────────────────────────────────────────────────────────────────────
col_left, col_right = st.columns(2)

with col_left:
    st.subheader("By Category")
    cat_counts = open_df["category"].value_counts().reset_index()
    cat_counts.columns = ["Category", "Count"]
    fig = px.bar(cat_counts, x="Count", y="Category", orientation="h",
                 color="Count", color_continuous_scale="Blues")
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=300)
    st.plotly_chart(fig, use_container_width=True)

with col_right:
    st.subheader("By Status")
    status_counts = df["status"].value_counts().reset_index()
    status_counts.columns = ["Status", "Count"]
    fig2 = px.pie(status_counts, names="Status", values="Count", hole=0.4)
    fig2.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=300)
    st.plotly_chart(fig2, use_container_width=True)

st.divider()

# ── Filters + Table ───────────────────────────────────────────────────────────
st.subheader("Grievances")

f1, f2, f3 = st.columns(3)
sel_status   = f1.selectbox("Status", ["All"] + STATUS_ORDER)
sel_urgency  = f2.selectbox("Urgency", ["All", "critical", "high", "medium", "low"])
sel_category = f3.selectbox("Category", ["All"] + sorted(df["category"].unique().tolist()))

filtered = df.copy()
if sel_status   != "All": filtered = filtered[filtered["status"]   == sel_status]
if sel_urgency  != "All": filtered = filtered[filtered["urgency"]  == sel_urgency]
if sel_category != "All": filtered = filtered[filtered["category"] == sel_category]

display_cols = ["grievance_id", "filed_at", "urgency", "category", "status", "summary", "citizen_contact", "assigned_to"]
st.dataframe(
    filtered[display_cols].rename(columns={
        "grievance_id":    "Ref ID",
        "filed_at":        "Filed",
        "urgency":         "Urgency",
        "category":        "Category",
        "status":          "Status",
        "summary":         "Summary",
        "citizen_contact": "Contact",
        "assigned_to":     "Assigned To",
    }),
    use_container_width=True,
    height=400,
)

st.divider()

# ── Update Status Panel ───────────────────────────────────────────────────────
st.subheader("Update a Grievance")

grievance_ids = filtered["grievance_id"].tolist()
if not grievance_ids:
    st.info("No grievances match the current filter.")
else:
    sel_id = st.selectbox("Select Grievance", grievance_ids)
    row = filtered[filtered["grievance_id"] == sel_id].iloc[0]

    with st.form("update_form"):
        new_status   = st.selectbox("New Status", STATUS_ORDER, index=STATUS_ORDER.index(row["status"]))
        assigned_to  = st.text_input("Assigned To", value=row.get("assigned_to") or "")
        next_action  = st.text_input("Next Action", value=row.get("next_action") or "")
        submitted = st.form_submit_button("Update")

    if submitted:
        update_status(row["id"], new_status, assigned_to, next_action)
        st.success(f"Updated {sel_id} → {new_status}")
        st.cache_data.clear()
        st.rerun()

st.divider()

# ── Walk-in / Phone Intake ─────────────────────────────────────────────────
with st.expander("➕ Log a Walk-in / Phone / Letter Grievance"):
    st.caption("Use this form to register grievances received in person, by phone, or by letter.")
    with st.form("walkin_form"):
        wi_name    = st.text_input("Citizen Name (optional)")
        wi_contact = st.text_input("Citizen Phone Number (optional, with country code e.g. 919876543210)")
        wi_channel = st.selectbox("Channel", ["walk_in", "phone", "letter"])
        wi_text    = st.text_area("Grievance Description", height=120,
                                   placeholder="Describe the grievance as told by the citizen...")
        wi_submit  = st.form_submit_button("Register Grievance")

    if wi_submit:
        if not wi_text.strip():
            st.error("Please enter a grievance description.")
        else:
            with st.spinner("Classifying and registering..."):
                try:
                    payload = {
                        "office_id":       DEMO_OFFICE_ID,
                        "citizen_name":    wi_name or None,
                        "citizen_contact": wi_contact or None,
                        "channel":         wi_channel,
                        "raw_text":        wi_text.strip(),
                    }
                    resp = httpx.post(
                        f"{BACKEND_URL}/grievances/walkin",
                        json=payload,
                        timeout=30,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        st.success(f"Registered: **{data['grievance_id']}**")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.error(f"Backend error {resp.status_code}: {resp.text}")
                except Exception as e:
                    st.error(f"Could not reach backend: {e}")
