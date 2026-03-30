"""
Jan-Sunwai Dashboard  — v2
---------------------------
Streamlit app for constituency office staff.
Professional redesign: Inter font, government blue palette, card KPIs,
sidebar filters, consistent Plotly theme.

Run locally:
    streamlit run dashboard/app.py
"""

import os
from datetime import datetime, timezone, timedelta

import httpx
import pandas as pd
import plotly.express as px
import streamlit as st
from supabase import create_client

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Jan-Sunwai | Pollity.in",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

*, html, body, [class*="css"] { font-family: 'Inter', sans-serif !important; }

/* Hide default chrome */
#MainMenu { visibility: hidden; }
footer    { visibility: hidden; }
.stDeployButton { display: none; }

/* Top banner */
.top-banner {
    background: linear-gradient(120deg, #1565C0 0%, #0D47A1 100%);
    border-radius: 12px;
    padding: 1.4rem 2rem;
    color: white;
    margin-bottom: 1.5rem;
    display: flex;
    justify-content: space-between;
    align-items: center;
}
.top-banner h1 { margin: 0; font-size: 1.5rem; font-weight: 700; letter-spacing: -0.3px; }
.top-banner p  { margin: 4px 0 0; font-size: 0.82rem; opacity: 0.75; }
.top-banner .pill {
    background: rgba(255,255,255,0.15);
    border: 1px solid rgba(255,255,255,0.3);
    border-radius: 20px;
    padding: 5px 16px;
    font-size: 0.78rem;
    font-weight: 500;
    white-space: nowrap;
}

/* KPI cards */
.kpi-row { display: flex; gap: 1rem; margin-bottom: 1.5rem; }
.kpi-card {
    flex: 1;
    background: #ffffff;
    border-radius: 10px;
    padding: 1.2rem 1.5rem;
    border-left: 4px solid #1565C0;
    box-shadow: 0 1px 4px rgba(0,0,0,0.07);
}
.kpi-card.red   { border-left-color: #c62828; }
.kpi-card.amber { border-left-color: #e65100; }
.kpi-card.green { border-left-color: #2e7d32; }
.kpi-card.blue  { border-left-color: #1565C0; }
.kpi-label {
    font-size: 0.68rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.7px; color: #888; margin-bottom: 6px;
}
.kpi-value { font-size: 2.2rem; font-weight: 700; line-height: 1; color: #111; }
.kpi-card.red   .kpi-value { color: #c62828; }
.kpi-card.amber .kpi-value { color: #e65100; }
.kpi-card.green .kpi-value { color: #2e7d32; }
.kpi-sub { font-size: 0.75rem; color: #aaa; margin-top: 4px; }

/* Section titles */
.sec-title {
    font-size: 0.72rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.8px; color: #1565C0;
    border-bottom: 2px solid #E3F2FD;
    padding-bottom: 6px; margin-bottom: 14px; margin-top: 4px;
}

/* Grievance detail card */
.detail-card {
    background: #F8FAFF;
    border: 1px solid #E3F2FD;
    border-radius: 10px;
    padding: 1rem 1.25rem;
    margin-bottom: 1rem;
    line-height: 1.8;
    font-size: 0.9rem;
}
.detail-card b { color: #444; }

/* Sidebar — force light regardless of user theme setting */
section[data-testid="stSidebar"] {
    background-color: #EEF2FF !important;
}
section[data-testid="stSidebar"] * {
    color: #1a1a1a !important;
}
section[data-testid="stSidebar"] h3 {
    color: #1565C0 !important;
    font-weight: 700 !important;
}
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] caption {
    color: #333 !important;
}
/* Force light background on sidebar selectboxes */
section[data-testid="stSidebar"] [data-baseweb="select"] > div {
    background-color: #ffffff !important;
    border-color: #CBD5E0 !important;
    color: #1a1a1a !important;
}
section[data-testid="stSidebar"] [data-baseweb="select"] span {
    color: #1a1a1a !important;
}
/* Selectbox dropdown menu */
[data-baseweb="popover"] * {
    color: #1a1a1a !important;
    background-color: #ffffff !important;
}
</style>
""", unsafe_allow_html=True)

# ── Config ────────────────────────────────────────────────────────────────────
def _get(key, default=""):
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, default)

SUPABASE_URL   = _get("SUPABASE_URL")
SUPABASE_KEY   = _get("SUPABASE_SERVICE_ROLE_KEY") or _get("SUPABASE_ANON_KEY")
DEMO_OFFICE_ID = _get("DEMO_OFFICE_ID")
BACKEND_URL    = _get("BACKEND_URL", "https://pollitymvp-production.up.railway.app")

STATUS_ORDER = [
    "registered", "acknowledged", "assigned",
    "in_progress", "resolved", "verified", "closed",
]
CATEGORY_LABELS = {
    "infrastructure":  "Infrastructure",
    "welfare_schemes": "Welfare Schemes",
    "public_safety":   "Public Safety",
    "healthcare":      "Healthcare",
    "education":       "Education",
    "land_revenue":    "Land & Revenue",
    "corruption":      "Corruption",
    "others":          "Others",
}
CHART_BLUES = ["#0D47A1","#1565C0","#1976D2","#1E88E5","#42A5F5","#90CAF9","#BBDEFB","#E3F2FD"]
URGENCY_COLOR = {
    "critical": "#c62828", "high": "#e65100", "medium": "#1565C0", "low": "#2e7d32",
}


@st.cache_resource
def get_supabase():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


@st.cache_data(ttl=60)
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
    df["category_label"] = df["category"].map(CATEGORY_LABELS).fillna(df["category"])
    return df


def update_status(grievance_uuid: str, new_status: str, assigned_to: str, next_action: str):
    resp = httpx.patch(
        f"{BACKEND_URL}/grievances/{grievance_uuid}/status",
        json={"status": new_status, "assigned_to": assigned_to, "next_action": next_action},
        timeout=15,
    )
    if resp.status_code != 200:
        st.error(f"Update failed: {resp.text}")


# ── Header ────────────────────────────────────────────────────────────────────
IST = timezone(timedelta(hours=5, minutes=30))
now_str = datetime.now(tz=IST).strftime("%d %b %Y · %I:%M %p IST")
st.markdown(f"""
<div class="top-banner">
  <div>
    <h1>🏛️ Jan-Sunwai</h1>
    <p>Grievance Operations Dashboard &nbsp;·&nbsp; Pollity.in</p>
  </div>
  <div class="pill">🕐 {now_str}</div>
</div>
""", unsafe_allow_html=True)

if not DEMO_OFFICE_ID:
    st.error("DEMO_OFFICE_ID is not configured.")
    st.stop()

df = load_grievances(DEMO_OFFICE_ID)

if df.empty:
    st.info("No grievances registered yet. Send a WhatsApp message to get started.")
    st.stop()

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🔍 Filters")
    sel_status   = st.selectbox("Status",   ["All"] + STATUS_ORDER)
    sel_urgency  = st.selectbox("Urgency",  ["All", "critical", "high", "medium", "low"])
    sel_category = st.selectbox("Category", ["All"] + sorted(df["category"].unique().tolist()))

    st.divider()
    if st.button("🔄 Refresh Data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption(f"Auto-refreshes every 60 s")

    st.divider()
    st.markdown("### ➕ Log a Grievance")
    with st.expander("Walk-in / Phone / Letter"):
        st.caption("Register grievances received in person, by phone, or by letter.")
        with st.form("walkin_form"):
            wi_name    = st.text_input("Citizen Name (optional)")
            wi_contact = st.text_input("Phone (optional, e.g. 919876543210)")
            wi_channel = st.selectbox("Channel", ["walk_in", "phone", "letter"])
            wi_image   = st.file_uploader(
                "Letter image (JPEG/PNG ≤ 5 MB)",
                type=["jpg","jpeg","png","gif","webp"],
                help="For Letter channel — OCR extracts text automatically.",
            )
            wi_text    = st.text_area("Description", height=100,
                placeholder="Describe the grievance or type letter contents.")
            wi_submit  = st.form_submit_button("Register", use_container_width=True, type="primary")

        if wi_submit:
            use_ocr = wi_channel == "letter" and wi_image is not None
            if not use_ocr and not wi_text.strip():
                st.error("Enter a description or upload a letter image.")
            else:
                with st.spinner("Processing..."):
                    try:
                        if use_ocr:
                            resp = httpx.post(
                                f"{BACKEND_URL}/grievances/letter-ocr",
                                data={
                                    "office_id":       DEMO_OFFICE_ID,
                                    "citizen_name":    wi_name or "",
                                    "citizen_contact": wi_contact or "",
                                },
                                files={"image": (wi_image.name, wi_image.getvalue(), wi_image.type)},
                                timeout=60,
                            )
                        else:
                            resp = httpx.post(
                                f"{BACKEND_URL}/grievances/walkin",
                                json={
                                    "office_id":       DEMO_OFFICE_ID,
                                    "citizen_name":    wi_name or None,
                                    "citizen_contact": wi_contact or None,
                                    "channel":         wi_channel,
                                    "raw_text":        wi_text.strip(),
                                },
                                timeout=30,
                            )
                        if resp.status_code == 200:
                            data = resp.json()
                            st.success(f"Registered: **{data['grievance_id']}**")
                            if use_ocr and data.get("ocr_text"):
                                with st.expander("OCR text"):
                                    st.text(data["ocr_text"])
                            st.cache_data.clear()
                            st.rerun()
                        else:
                            st.error(f"Error {resp.status_code}: {resp.text}")
                    except Exception as e:
                        st.error(f"Could not reach backend: {e}")

# ── Compute KPIs ──────────────────────────────────────────────────────────────
open_df        = df[df["status"] != "closed"]
critical_count = int((open_df["urgency"] == "critical").sum())
high_count     = int((open_df["urgency"] == "high").sum())
resolved_today = df[
    df["status"].isin(["resolved","verified","closed"]) &
    (df["filed_at"].dt.date == pd.Timestamp.today().date())
].shape[0]

# Apply filters
filtered = df.copy()
if sel_status   != "All": filtered = filtered[filtered["status"]   == sel_status]
if sel_urgency  != "All": filtered = filtered[filtered["urgency"]  == sel_urgency]
if sel_category != "All": filtered = filtered[filtered["category"] == sel_category]

# ── KPI Cards ─────────────────────────────────────────────────────────────────
st.markdown(f"""
<div class="kpi-row">
  <div class="kpi-card blue">
    <div class="kpi-label">Total Grievances</div>
    <div class="kpi-value">{len(df)}</div>
    <div class="kpi-sub">all time</div>
  </div>
  <div class="kpi-card amber">
    <div class="kpi-label">Open</div>
    <div class="kpi-value">{len(open_df)}</div>
    <div class="kpi-sub">pending resolution</div>
  </div>
  <div class="kpi-card red">
    <div class="kpi-label">Critical</div>
    <div class="kpi-value">{critical_count}</div>
    <div class="kpi-sub">{high_count} high priority</div>
  </div>
  <div class="kpi-card green">
    <div class="kpi-label">Resolved Today</div>
    <div class="kpi-value">{resolved_today}</div>
    <div class="kpi-sub">verified + closed</div>
  </div>
</div>
""", unsafe_allow_html=True)

# ── Charts ────────────────────────────────────────────────────────────────────
col_left, col_right = st.columns(2)

with col_left:
    st.markdown('<div class="sec-title">Open Grievances by Category</div>', unsafe_allow_html=True)
    cat_counts = open_df["category_label"].value_counts().reset_index()
    cat_counts.columns = ["Category", "Count"]
    fig = px.bar(
        cat_counts, x="Count", y="Category", orientation="h",
        color="Count", color_continuous_scale=[[0,"#90CAF9"],[1,"#0D47A1"]],
        text="Count",
    )
    fig.update_layout(
        margin=dict(l=0, r=20, t=4, b=0), height=280,
        plot_bgcolor="white", paper_bgcolor="white",
        coloraxis_showscale=False,
        xaxis=dict(gridcolor="#F0F4F8", title="", color="#333"),
        yaxis=dict(title="", color="#333"),
        font=dict(family="Inter", size=12, color="#333"),
    )
    fig.update_traces(textposition="outside", marker_line_width=0,
                      textfont=dict(size=11, color="#333"))
    st.plotly_chart(fig, use_container_width=True)

with col_right:
    st.markdown('<div class="sec-title">Grievances by Status</div>', unsafe_allow_html=True)
    status_counts = df["status"].value_counts().reset_index()
    status_counts.columns = ["Status", "Count"]
    fig2 = px.pie(
        status_counts, names="Status", values="Count",
        hole=0.58, color_discrete_sequence=CHART_BLUES,
    )
    fig2.update_layout(
        margin=dict(l=0, r=0, t=4, b=0), height=280,
        paper_bgcolor="white",
        legend=dict(
            orientation="v", x=1.0, y=0.5,
            font=dict(size=11, color="#333"),
            bgcolor="white",
        ),
        font=dict(family="Inter", color="#333"),
    )
    fig2.update_traces(
        textposition="inside",
        textfont=dict(size=11, color="white"),
        insidetextorientation="radial",
    )
    st.plotly_chart(fig2, use_container_width=True)

# ── Grievance Table ───────────────────────────────────────────────────────────
st.markdown('<div class="sec-title">Grievances</div>', unsafe_allow_html=True)
st.caption(f"Showing **{len(filtered)}** of **{len(df)}** total")

display_cols = [
    "grievance_id","filed_at","urgency","category_label",
    "status","summary","citizen_contact","assigned_to",
]
st.dataframe(
    filtered[display_cols].rename(columns={
        "grievance_id":    "Ref ID",
        "filed_at":        "Filed",
        "urgency":         "Urgency",
        "category_label":  "Category",
        "status":          "Status",
        "summary":         "Summary",
        "citizen_contact": "Contact",
        "assigned_to":     "Assigned To",
    }),
    use_container_width=True,
    height=380,
    hide_index=True,
    column_config={
        "Ref ID":     st.column_config.TextColumn("Ref ID",    width="medium"),
        "Filed":      st.column_config.DatetimeColumn("Filed", format="DD MMM, hh:mm a"),
        "Urgency":    st.column_config.TextColumn("Urgency",   width="small"),
        "Category":   st.column_config.TextColumn("Category",  width="medium"),
        "Status":     st.column_config.TextColumn("Status",    width="medium"),
        "Summary":    st.column_config.TextColumn("Summary",   width="large"),
        "Contact":    st.column_config.TextColumn("Contact",   width="medium"),
        "Assigned To":st.column_config.TextColumn("Assigned To", width="medium"),
    },
)

# ── Update Status ─────────────────────────────────────────────────────────────
st.markdown('<div class="sec-title" style="margin-top:1.5rem">Update a Grievance</div>',
            unsafe_allow_html=True)

grievance_ids = filtered["grievance_id"].tolist()
if not grievance_ids:
    st.info("No grievances match the current filter.")
else:
    sel_id = st.selectbox("Select grievance to update", grievance_ids)
    row = filtered[filtered["grievance_id"] == sel_id].iloc[0]

    urgency_color = URGENCY_COLOR.get(row["urgency"], "#888")
    st.markdown(f"""
    <div class="detail-card">
      <b>Summary:</b> {row['summary']}<br>
      <b>Category:</b> {CATEGORY_LABELS.get(row['category'], row['category'])} &nbsp;·&nbsp;
      <b>Urgency:</b> <span style="color:{urgency_color};font-weight:600">{row['urgency'].upper()}</span> &nbsp;·&nbsp;
      <b>Contact:</b> {row['citizen_contact']} &nbsp;·&nbsp;
      <b>Filed:</b> {row['filed_at'].strftime('%d %b %Y, %I:%M %p')}
    </div>
    """, unsafe_allow_html=True)

    with st.form("update_form"):
        uf1, uf2, uf3 = st.columns(3)
        new_status  = uf1.selectbox("New Status", STATUS_ORDER,
                                    index=STATUS_ORDER.index(str(row["status"])))
        assigned_to = uf2.text_input("Assigned To", value=row.get("assigned_to") or "")
        next_action = uf3.text_input("Next Action", value=row.get("next_action") or "")
        submitted   = st.form_submit_button("Update Grievance", use_container_width=True, type="primary")

    if submitted:
        update_status(row["id"], new_status, assigned_to, next_action)
        st.success(f"**{sel_id}** → **{new_status}**")
        st.cache_data.clear()
        st.rerun()
