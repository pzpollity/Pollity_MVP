"""
Jan Sunn Dashboard — v3
-------------------------
UI/UX redesign: dark sidebar, tabbed navigation, inline SVG logo,
modern KPI cards with gradient accents, urgency badges, spacious
log-grievance form in its own tab.

Run locally:
    streamlit run dashboard/app.py
"""

import os
import time
from datetime import datetime, timezone, timedelta

import httpx
import pandas as pd
import plotly.express as px
import streamlit as st
from supabase import create_client

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Jan Sunn | NetaWork.in",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Auto-refresh every 60 s ───────────────────────────────────────────────────
if "last_refresh" not in st.session_state:
    st.session_state["last_refresh"] = time.time()
if time.time() - st.session_state["last_refresh"] >= 60:
    st.session_state["last_refresh"] = time.time()
    st.rerun()

# ── Inline SVG assets ─────────────────────────────────────────────────────────

# ── Global CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

*, html, body, [class*="css"] { font-family: 'Inter', sans-serif !important; }
#MainMenu { visibility: hidden; }
footer    { visibility: hidden; }
.stDeployButton { display: none; }

/* ── Global bg */
.stApp { background: #EEF2FF !important; }
.block-container { padding-top: 1.2rem !important; padding-bottom: 2rem !important; }

/* ── HEADER ──────────────────────────────────────────────────────────────── */
.js-header {
    background: linear-gradient(135deg, #0D47A1 0%, #1565C0 55%, #1976D2 100%);
    border-radius: 18px;
    padding: 1.25rem 2rem;
    color: white;
    margin-bottom: 1.6rem;
    display: flex;
    justify-content: space-between;
    align-items: center;
    box-shadow: 0 6px 24px rgba(21,101,192,0.38);
    position: relative;
    overflow: hidden;
}
.js-header::before {
    content: '';
    position: absolute; top: -50px; right: -30px;
    width: 220px; height: 220px; border-radius: 50%;
    background: rgba(255,255,255,0.07);
    pointer-events: none;
}
.js-header::after {
    content: '';
    position: absolute; bottom: -70px; right: 120px;
    width: 180px; height: 180px; border-radius: 50%;
    background: rgba(255,255,255,0.04);
    pointer-events: none;
}
.js-header-left  { display: flex; align-items: center; gap: 0; z-index: 1; }
.js-header-right { display: flex; align-items: center; gap: 10px; z-index: 1; }
.js-header-title { font-size: 1.7rem; font-weight: 800; letter-spacing: -0.5px; margin: 0; line-height: 1.1; }
.js-header-sub   { font-size: 0.78rem; opacity: 0.68; margin: 4px 0 0; font-weight: 400; letter-spacing: 0.2px; }
.js-pill {
    background: rgba(255,255,255,0.14);
    backdrop-filter: blur(8px);
    border: 1px solid rgba(255,255,255,0.28);
    border-radius: 24px;
    padding: 6px 18px;
    font-size: 0.78rem;
    font-weight: 600;
    white-space: nowrap;
}
.js-live-dot {
    display: inline-block; width: 7px; height: 7px;
    background: #4ADE80; border-radius: 50%; margin-right: 5px;
    animation: livepulse 2s ease-in-out infinite;
}
@keyframes livepulse { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:.4;transform:scale(.85)} }

/* ── DARK SIDEBAR ────────────────────────────────────────────────────────── */
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0F172A 0%, #1A2744 100%) !important;
    border-right: none !important;
    min-width: 240px !important;
}
section[data-testid="stSidebar"] > div { padding-top: 0 !important; }
section[data-testid="stSidebar"] * { color: #CBD5E1 !important; }

/* sidebar section labels */
.sb-section-label {
    font-size: 0.65rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 1.2px; color: #475569;
    margin: 1.2rem 0 0.6rem; padding: 0;
}

/* sidebar selects */
section[data-testid="stSidebar"] [data-baseweb="select"] > div {
    background: rgba(255,255,255,0.06) !important;
    border: 1px solid rgba(255,255,255,0.10) !important;
    border-radius: 8px !important; color: #E2E8F0 !important;
}
section[data-testid="stSidebar"] [data-baseweb="select"] span { color: #E2E8F0 !important; }
section[data-testid="stSidebar"] [data-baseweb="select"] svg  { fill: #64748B !important; }
[data-baseweb="popover"] * { color: #0F172A !important; background: #fff !important; }

/* sidebar divider */
section[data-testid="stSidebar"] hr { border-color: rgba(255,255,255,0.08) !important; margin: 0.8rem 0 !important; }
section[data-testid="stSidebar"] .stCaption { color: #475569 !important; font-size: 0.72rem !important; }

/* sidebar button */
section[data-testid="stSidebar"] .stButton > button {
    background: rgba(21,101,192,0.75) !important;
    color: #E0EFFF !important;
    border: 1px solid rgba(21,101,192,0.5) !important;
    border-radius: 10px !important;
    font-weight: 600 !important;
    font-size: 0.82rem !important;
    transition: background 0.15s !important;
}
section[data-testid="stSidebar"] .stButton > button:hover {
    background: #1565C0 !important;
}
section[data-testid="stSidebar"] label { color: #94A3B8 !important; font-size: 0.78rem !important; }

/* sidebar logo strip */
.sb-logo-strip {
    padding: 1.2rem 0 1rem;
    border-bottom: 1px solid rgba(255,255,255,0.07);
    margin-bottom: 0.4rem;
}

/* ── TABS ────────────────────────────────────────────────────────────────── */
.stTabs [data-baseweb="tab-list"] {
    background: transparent !important;
    gap: 2px;
    border-bottom: 2px solid #E2E8F0 !important;
}
.stTabs [data-baseweb="tab"] {
    background: transparent !important;
    border-radius: 10px 10px 0 0 !important;
    padding: 10px 24px !important;
    font-size: 0.86rem !important;
    font-weight: 600 !important;
    color: #64748B !important;
    border-bottom: 3px solid transparent !important;
    margin-bottom: -2px !important;
}
.stTabs [aria-selected="true"] {
    color: #1565C0 !important;
    border-bottom: 3px solid #1565C0 !important;
    background: white !important;
}
.stTabs [data-baseweb="tab-panel"] { padding-top: 1.5rem !important; }

/* ── KPI CARDS ───────────────────────────────────────────────────────────── */
.kpi-grid { display: grid; grid-template-columns: repeat(5,1fr); gap: 12px; margin-bottom: 1.4rem; }
.kpi-card {
    background: #fff;
    border-radius: 14px;
    padding: 1.1rem 1.2rem 1rem;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06), 0 0 0 1px rgba(0,0,0,0.04);
    position: relative; overflow: hidden;
}
.kpi-card::before {
    content: ''; position: absolute; top: 0; left: 0; right: 0;
    height: 3.5px; border-radius: 14px 14px 0 0;
}
.kpi-blue::before   { background: linear-gradient(90deg,#1565C0,#42A5F5); }
.kpi-amber::before  { background: linear-gradient(90deg,#D97706,#FCD34D); }
.kpi-red::before    { background: linear-gradient(90deg,#DC2626,#F87171); }
.kpi-green::before  { background: linear-gradient(90deg,#16A34A,#4ADE80); }
.kpi-purple::before { background: linear-gradient(90deg,#7C3AED,#C084FC); }

.kpi-label { font-size: 0.64rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.9px; color: #94A3B8; margin-bottom: 2px; }
.kpi-value { font-size: 2.4rem; font-weight: 800; line-height: 1.05; color: #0F172A; }
.kpi-blue  .kpi-value  { color: #1565C0; }
.kpi-amber .kpi-value  { color: #D97706; }
.kpi-red   .kpi-value  { color: #DC2626; }
.kpi-green .kpi-value  { color: #16A34A; }
.kpi-purple .kpi-value { color: #7C3AED; }
.kpi-sub { font-size: 0.72rem; color: #94A3B8; margin-top: 5px; }

@keyframes pulse-red { 0%{box-shadow:0 0 0 0 rgba(220,38,38,.35)} 70%{box-shadow:0 0 0 10px rgba(220,38,38,0)} 100%{box-shadow:0 0 0 0 rgba(220,38,38,0)} }
.kpi-red.pulsing { animation: pulse-red 2.2s ease-out infinite; }

/* ── SECTION TITLE ───────────────────────────────────────────────────────── */
.sec-title {
    font-size: 0.7rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.9px; color: #1565C0;
    border-bottom: 2px solid #E3F2FD;
    padding-bottom: 8px; margin: 0 0 14px;
    display: flex; align-items: center; gap: 6px;
}

/* ── CHART CARD ──────────────────────────────────────────────────────────── */
.chart-card {
    background: #fff;
    border-radius: 14px;
    padding: 1.2rem 1.4rem 0.6rem;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06), 0 0 0 1px rgba(0,0,0,0.04);
}
.chart-title {
    font-size: 0.78rem; font-weight: 700; color: #0F172A;
    text-transform: uppercase; letter-spacing: 0.6px; margin-bottom: 2px;
}
.chart-sub { font-size: 0.72rem; color: #94A3B8; margin-bottom: 12px; }

/* ── DETAIL CARD ─────────────────────────────────────────────────────────── */
.detail-card {
    background: #F8FAFF;
    border: 1px solid #DBEAFE;
    border-left: 4px solid #1565C0;
    border-radius: 12px;
    padding: 1rem 1.3rem;
    margin-bottom: 1rem;
    line-height: 2;
    font-size: 0.88rem;
}

/* ── URGENCY BADGES ──────────────────────────────────────────────────────── */
.badge { display:inline-block; padding:2px 10px; border-radius:20px; font-size:0.7rem; font-weight:700; text-transform:uppercase; letter-spacing:0.5px; }
.badge-critical { background:#FEE2E2; color:#DC2626; }
.badge-high     { background:#FEF3C7; color:#B45309; }
.badge-medium   { background:#DBEAFE; color:#1D4ED8; }
.badge-low      { background:#DCFCE7; color:#15803D; }

/* ── LOG FORM (Tab 3) ────────────────────────────────────────────────────── */
.log-hero {
    background: linear-gradient(135deg, #0D47A1 0%, #1565C0 60%, #1E88E5 100%);
    border-radius: 16px; padding: 1.5rem 2rem; color: white;
    margin-bottom: 1.8rem;
    display: flex; align-items: center; gap: 1.2rem;
    box-shadow: 0 4px 20px rgba(21,101,192,0.28);
}
.log-hero-icon  { font-size: 2.2rem; line-height: 1; }
.log-hero-title { font-size: 1.25rem; font-weight: 800; letter-spacing: -0.3px; margin: 0; }
.log-hero-sub   { font-size: 0.8rem; opacity: 0.7; margin: 3px 0 0; }

.log-section-label {
    font-size: 0.68rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.8px; color: #64748B;
    display: flex; align-items: center; gap: 8px;
    border-bottom: 2px solid #E2E8F0;
    padding-bottom: 8px; margin-bottom: 12px; margin-top: 1.6rem;
}
.log-step-badge {
    display: inline-flex; align-items: center; justify-content: center;
    width: 22px; height: 22px; border-radius: 50%;
    background: #1565C0; color: white; font-size: 0.68rem; font-weight: 700;
    flex-shrink: 0;
}
.ai-banner {
    background: #EFF6FF; border: 1px solid #BFDBFE;
    border-left: 4px solid #3B82F6; border-radius: 10px;
    padding: 0.85rem 1rem; font-size: 0.82rem; color: #1E40AF;
    margin-top: 1.4rem; line-height: 1.5;
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

STATUS_ORDER = ["registered","acknowledged","assigned","in_progress","resolved","verified","closed"]
SLA_HOURS    = {"critical": 24, "high": 72, "medium": 168, "low": 504}
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
URGENCY_COLOR = {"critical":"#DC2626","high":"#D97706","medium":"#1565C0","low":"#16A34A"}


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
    df["filed_at"] = pd.to_datetime(df["filed_at"], utc=True).dt.tz_convert("Asia/Kolkata")
    df["status"] = pd.Categorical(df["status"], categories=STATUS_ORDER, ordered=True)
    df["category_label"] = df["category"].map(CATEGORY_LABELS).fillna(df["category"])

    now_ist = pd.Timestamp.now(tz="Asia/Kolkata")
    df["sla_hours"]    = df["urgency"].map(SLA_HOURS).fillna(168)
    df["sla_deadline"] = df["filed_at"] + pd.to_timedelta(df["sla_hours"], unit="h")
    df["hours_open"]   = (now_ist - df["filed_at"]).dt.total_seconds() / 3600
    is_open = ~df["status"].isin(["resolved","verified","closed"])
    df["sla_status"] = "closed"
    df.loc[is_open & (df["hours_open"] <= df["sla_hours"] * 0.75),                              "sla_status"] = "on_time"
    df.loc[is_open & (df["hours_open"] >  df["sla_hours"] * 0.75) & (df["hours_open"] <= df["sla_hours"]), "sla_status"] = "at_risk"
    df.loc[is_open & (df["hours_open"] >  df["sla_hours"]),                                     "sla_status"] = "breached"
    return df


def update_status(grievance_uuid: str, new_status: str, assigned_to: str, next_action: str):
    resp = httpx.patch(
        f"{BACKEND_URL}/grievances/{grievance_uuid}/status",
        json={"status": new_status, "assigned_to": assigned_to, "next_action": next_action},
        timeout=15,
    )
    if resp.status_code != 200:
        st.error(f"Update failed: {resp.text}")


# ── HEADER ────────────────────────────────────────────────────────────────────
IST     = timezone(timedelta(hours=5, minutes=30))
now_str = datetime.now(tz=IST).strftime("%d %b %Y · %I:%M %p IST")

st.markdown(f"""
<div class="js-header">
  <div class="js-header-left">
    <div>
      <div class="js-header-title">Jan Sunn</div>
      <div class="js-header-sub">Constituency Grievance Dashboard &nbsp;·&nbsp; NetaWork.in</div>
    </div>
  </div>
  <div class="js-header-right">
    <div class="js-pill"><span class="js-live-dot"></span>Live</div>
    <div class="js-pill">🕐 {now_str}</div>
  </div>
</div>
""", unsafe_allow_html=True)

if not DEMO_OFFICE_ID:
    st.error("DEMO_OFFICE_ID is not configured.")
    st.stop()

df = load_grievances(DEMO_OFFICE_ID)

if df.empty:
    st.info("No grievances registered yet. Send a WhatsApp message to get started.")
    st.stop()

# ── SIDEBAR ───────────────────────────────────────────────────────────────────
with st.sidebar:
    # Sidebar title
    st.markdown("""
    <div class="sb-logo-strip">
      <div style="font-size:1.1rem;font-weight:800;color:#F1F5F9;letter-spacing:-0.3px;">Jan Sunn</div>
      <div style="font-size:0.7rem;color:#475569;letter-spacing:1.5px;margin-top:2px;">NETAWORK.IN</div>
    </div>
    """, unsafe_allow_html=True)

    # Filters
    st.markdown('<div class="sb-section-label">Filters</div>', unsafe_allow_html=True)
    sel_status   = st.selectbox("Status",   ["All"] + STATUS_ORDER, label_visibility="collapsed")
    sel_urgency  = st.selectbox("Urgency",  ["All","critical","high","medium","low"], label_visibility="collapsed")
    sel_category = st.selectbox("Category", ["All"] + sorted(df["category"].unique().tolist()), label_visibility="collapsed")

    st.divider()
    if st.button("↺  Refresh Data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption("⚡ Auto-refreshes every 60 s")

# ── COMPUTE KPIs ──────────────────────────────────────────────────────────────
open_df        = df[~df["status"].isin(["resolved","verified","closed"])]
critical_count = int((open_df["urgency"] == "critical").sum())
high_count     = int((open_df["urgency"] == "high").sum())
resolved_today = df[
    df["status"].isin(["resolved","verified","closed"]) &
    (df["filed_at"].dt.date == pd.Timestamp.today().date())
].shape[0]
sla_breached = int((open_df["sla_status"] == "breached").sum())
sla_at_risk  = int((open_df["sla_status"] == "at_risk").sum())

# Apply filters
filtered = df.copy()
if sel_status   != "All": filtered = filtered[filtered["status"]   == sel_status]
if sel_urgency  != "All": filtered = filtered[filtered["urgency"]  == sel_urgency]
if sel_category != "All": filtered = filtered[filtered["category"] == sel_category]

# ── TABS ──────────────────────────────────────────────────────────────────────
tab_overview, tab_grievances, tab_log = st.tabs([
    "Overview",
    "Grievances",
    "Log Grievance",
])

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — OVERVIEW
# ═══════════════════════════════════════════════════════════════════════════════
with tab_overview:

    # ── KPI cards ─────────────────────────────────────────────────────────────
    critical_pulse = " pulsing" if critical_count > 0 else ""
    st.markdown(f"""
    <div class="kpi-grid">
      <div class="kpi-card kpi-blue">
        <div class="kpi-label">Total Grievances</div>
        <div class="kpi-value">{len(df)}</div>
        <div class="kpi-sub">all time</div>
      </div>
      <div class="kpi-card kpi-amber">
        <div class="kpi-label">Open</div>
        <div class="kpi-value">{len(open_df)}</div>
        <div class="kpi-sub">pending resolution</div>
      </div>
      <div class="kpi-card kpi-red{critical_pulse}">
        <div class="kpi-label">Critical</div>
        <div class="kpi-value">{critical_count}</div>
        <div class="kpi-sub">{high_count} high priority</div>
      </div>
      <div class="kpi-card kpi-green">
        <div class="kpi-label">Resolved Today</div>
        <div class="kpi-value">{resolved_today}</div>
        <div class="kpi-sub">verified + closed</div>
      </div>
      <div class="kpi-card kpi-purple">
        <div class="kpi-label">SLA Breached</div>
        <div class="kpi-value">{sla_breached}</div>
        <div class="kpi-sub">{sla_at_risk} at risk</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Charts ────────────────────────────────────────────────────────────────
    col_l, col_r = st.columns(2, gap="medium")

    with col_l:
        st.markdown('<div class="chart-card">', unsafe_allow_html=True)
        st.markdown('<div class="chart-title">Open Grievances by Category</div>', unsafe_allow_html=True)
        st.markdown('<div class="chart-sub">Active cases requiring attention</div>', unsafe_allow_html=True)
        cat_counts = open_df["category_label"].value_counts().reset_index()
        cat_counts.columns = ["Category", "Count"]
        fig = px.bar(
            cat_counts, x="Count", y="Category", orientation="h",
            color="Count",
            color_continuous_scale=[[0,"#BFDBFE"],[0.5,"#1E88E5"],[1,"#0D47A1"]],
            text="Count",
        )
        fig.update_layout(
            plot_bgcolor="white", paper_bgcolor="white",
            margin=dict(l=0, r=10, t=4, b=0), height=270,
            font=dict(family="Inter", size=12, color="#334155"),
            coloraxis_showscale=False,
            xaxis=dict(gridcolor="#F1F5F9", linecolor="#E2E8F0", title=""),
            yaxis=dict(categoryorder="total ascending", title="", color="#334155", gridcolor="#F1F5F9"),
        )
        fig.update_traces(
            textposition="outside", marker_line_width=0,
            textfont=dict(size=11, color="#334155"),
        )
        st.plotly_chart(fig, use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)

    with col_r:
        st.markdown('<div class="chart-card">', unsafe_allow_html=True)
        st.markdown('<div class="chart-title">Grievances by Status</div>', unsafe_allow_html=True)
        st.markdown('<div class="chart-sub">Pipeline breakdown across all cases</div>', unsafe_allow_html=True)
        status_counts = df["status"].value_counts().reset_index()
        status_counts.columns = ["Status", "Count"]
        fig2 = px.pie(
            status_counts, names="Status", values="Count",
            hole=0.6, color_discrete_sequence=CHART_BLUES,
        )
        fig2.update_layout(
            margin=dict(l=0, r=0, t=4, b=0), height=270,
            paper_bgcolor="white",
            legend=dict(orientation="v", x=1.02, y=0.5,
                        font=dict(size=11, color="#334155"), bgcolor="white"),
            font=dict(family="Inter", color="#334155"),
        )
        fig2.update_traces(
            textposition="inside", textfont=dict(size=11, color="white"),
            insidetextorientation="radial",
        )
        st.plotly_chart(fig2, use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)

    # ── Urgency breakdown ─────────────────────────────────────────────────────
    st.markdown('<br>', unsafe_allow_html=True)
    col_a, col_b = st.columns(2, gap="medium")

    with col_a:
        st.markdown('<div class="chart-card">', unsafe_allow_html=True)
        st.markdown('<div class="chart-title">Open Cases by Urgency</div>', unsafe_allow_html=True)
        st.markdown('<div class="chart-sub">Distribution of active grievances</div>', unsafe_allow_html=True)
        urg_counts = open_df["urgency"].value_counts().reset_index()
        urg_counts.columns = ["Urgency", "Count"]
        fig3 = px.bar(
            urg_counts, x="Urgency", y="Count",
            color="Urgency",
            color_discrete_map=URGENCY_COLOR,
            text="Count",
        )
        fig3.update_layout(
            plot_bgcolor="white", paper_bgcolor="white",
            margin=dict(l=0, r=10, t=4, b=0), height=270,
            font=dict(family="Inter", size=12, color="#334155"),
            xaxis=dict(gridcolor="#F1F5F9", linecolor="#E2E8F0", title=""),
            yaxis=dict(gridcolor="#F1F5F9", linecolor="#E2E8F0", title=""),
            showlegend=False,
        )
        fig3.update_traces(
            textposition="outside", marker_line_width=0,
            textfont=dict(size=12, color="#334155"),
        )
        st.plotly_chart(fig3, use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)

    with col_b:
        st.markdown('<div class="chart-card">', unsafe_allow_html=True)
        st.markdown('<div class="chart-title">Grievances Filed Over Time</div>', unsafe_allow_html=True)
        st.markdown('<div class="chart-sub">Daily intake — last 30 days</div>', unsafe_allow_html=True)
        daily = (
            df.assign(date=df["filed_at"].dt.date)
            .groupby("date").size().reset_index(name="Count")
            .tail(30)
        )
        fig4 = px.area(
            daily, x="date", y="Count",
            line_shape="spline",
            color_discrete_sequence=["#1565C0"],
        )
        fig4.update_traces(
            fill="tozeroy",
            fillcolor="rgba(21,101,192,0.10)",
            line=dict(width=2.5),
        )
        fig4.update_layout(
            plot_bgcolor="white", paper_bgcolor="white",
            margin=dict(l=0, r=10, t=4, b=0), height=270,
            font=dict(family="Inter", size=12, color="#334155"),
            xaxis=dict(title="", color="#94A3B8", gridcolor="#F1F5F9", showgrid=False),
            yaxis=dict(title="", color="#94A3B8", gridcolor="#F1F5F9"),
        )
        st.plotly_chart(fig4, use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — GRIEVANCES
# ═══════════════════════════════════════════════════════════════════════════════
with tab_grievances:

    # ── Table ─────────────────────────────────────────────────────────────────
    st.markdown('<div class="sec-title">📋 Grievance Log</div>', unsafe_allow_html=True)
    st.caption(f"Showing **{len(filtered)}** of **{len(df)}** total  ·  filters applied in sidebar")

    _SLA_LABEL = {
        "on_time":  "✅ On Time",
        "at_risk":  "⚠️ At Risk",
        "breached": "🔴 Breached",
        "closed":   "—",
    }

    display_cols = [
        "grievance_id","filed_at","urgency","category_label",
        "status","sla_status","summary","citizen_contact","assigned_to",
    ]
    table_df = filtered[display_cols].copy()
    table_df["sla_status"] = table_df["sla_status"].map(_SLA_LABEL)

    st.dataframe(
        table_df.rename(columns={
            "grievance_id":   "Ref ID",
            "filed_at":       "Filed",
            "urgency":        "Urgency",
            "category_label": "Category",
            "status":         "Status",
            "sla_status":     "SLA",
            "summary":        "Summary",
            "citizen_contact":"Contact",
            "assigned_to":    "Assigned To",
        }),
        use_container_width=True,
        height=400,
        hide_index=True,
        column_config={
            "Ref ID":     st.column_config.TextColumn("Ref ID",    width="medium"),
            "Filed":      st.column_config.DatetimeColumn("Filed", format="DD MMM, hh:mm a"),
            "Urgency":    st.column_config.TextColumn("Urgency",   width="small"),
            "Category":   st.column_config.TextColumn("Category",  width="medium"),
            "Status":     st.column_config.TextColumn("Status",    width="medium"),
            "SLA":        st.column_config.TextColumn("SLA",       width="small"),
            "Summary":    st.column_config.TextColumn("Summary",   width="large"),
            "Contact":    st.column_config.TextColumn("Contact",   width="medium"),
            "Assigned To":st.column_config.TextColumn("Assigned To", width="medium"),
        },
    )

    # ── Update a grievance ────────────────────────────────────────────────────
    st.markdown('<br><div class="sec-title">✏️ Update a Grievance</div>', unsafe_allow_html=True)

    grievance_ids = filtered["grievance_id"].tolist()
    if not grievance_ids:
        st.info("No grievances match the current filter.")
    else:
        sel_id = st.selectbox("Select grievance to update", grievance_ids)
        row    = filtered[filtered["grievance_id"] == sel_id].iloc[0]
        urg    = row["urgency"]
        badge  = f'<span class="badge badge-{urg}">{urg.upper()}</span>'

        st.markdown(f"""
        <div class="detail-card">
          <b>Summary:</b> {row['summary']}<br>
          <b>Category:</b> {CATEGORY_LABELS.get(row['category'], row['category'])}
          &nbsp;&nbsp;{badge}&nbsp;&nbsp;
          <b>Contact:</b> {row['citizen_contact']}
          &nbsp;·&nbsp; <b>Filed:</b> {row['filed_at'].strftime('%d %b %Y, %I:%M %p')}
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
            contact      = row.get("citizen_contact", "")
            has_wa       = bool(contact) and contact not in ("WALK-IN", "")
            auto_notified = has_wa and new_status in {
                "acknowledged", "assigned", "in_progress", "resolved", "verified", "closed"
            }
            suffix = f"  ·  📲 WhatsApp update sent to {contact}" if auto_notified else ""
            st.success(f"**{sel_id}** → **{new_status}**{suffix}")
            st.cache_data.clear()
            st.rerun()



# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — LOG GRIEVANCE
# ═══════════════════════════════════════════════════════════════════════════════
with tab_log:

    # ── Hero banner ───────────────────────────────────────────────────────────
    st.markdown("""
    <div class="log-hero">
      <div class="log-hero-icon">📋</div>
      <div>
        <div class="log-hero-title">Log a New Grievance</div>
        <div class="log-hero-sub">Register citizens' complaints received in person, by phone, or by physical letter · AI classifies automatically</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Centred form ──────────────────────────────────────────────────────────
    _, form_col, _ = st.columns([1, 4, 1])
    with form_col:

        with st.form("walkin_form", border=False):

            # Step 1 — Citizen details
            st.markdown("""<div class="log-section-label">
              <span class="log-step-badge">1</span> Citizen Details
            </div>""", unsafe_allow_html=True)
            c1, c2 = st.columns(2)
            wi_name    = c1.text_input("Full Name",    placeholder="Full name  (optional)")
            wi_contact = c2.text_input("Phone Number", placeholder="e.g. 919876543210  (optional)")

            # Step 2 — Channel
            st.markdown("""<div class="log-section-label">
              <span class="log-step-badge">2</span> How did they come in?
            </div>""", unsafe_allow_html=True)
            wi_channel = st.radio(
                "Channel",
                ["walk_in", "phone", "letter"],
                format_func=lambda x: {
                    "walk_in": "Walk-in  —  citizen came in person",
                    "phone":   "Phone Call  —  complaint received via call",
                    "letter":  "Letter / Document  —  written complaint",
                }[x],
                horizontal=False,
                label_visibility="collapsed",
            )

            # Step 3 — Document upload
            st.markdown("""<div class="log-section-label">
              <span class="log-step-badge">3</span> Attach Document
              <span style="font-weight:400;color:#94A3B8;text-transform:none;letter-spacing:0">&nbsp;— optional, required for Letter channel</span>
            </div>""", unsafe_allow_html=True)
            wi_image = st.file_uploader(
                "Upload file",
                type=["jpg", "jpeg", "png", "gif", "webp", "pdf"],
                help="Accepted: JPEG, PNG, GIF, WEBP, PDF · Max 5 MB · AI extracts text automatically.",
                label_visibility="collapsed",
            )
            st.caption("Accepted: JPG · PNG · PDF · GIF · WEBP  ·  Max 5 MB  ·  Drag & drop or click to browse")

            # Step 4 — Description
            st.markdown("""<div class="log-section-label">
              <span class="log-step-badge">4</span> Grievance Description
            </div>""", unsafe_allow_html=True)
            wi_text = st.text_area(
                "Description",
                height=130,
                placeholder="Describe the issue in detail — what happened, where, how many people affected, any deadlines…",
                label_visibility="collapsed",
            )

            # AI tip
            st.markdown("""
            <div class="ai-banner">
              🤖&nbsp; <span><b>AI-powered:</b> Jan Sunn will automatically classify the category, urgency level,
              and generate a summary. For Letter channel, upload the document and leave the description
              blank — text is extracted via OCR.</span>
            </div>
            """, unsafe_allow_html=True)

            st.markdown("<div style='margin-top:1.4rem'></div>", unsafe_allow_html=True)
            wi_submit = st.form_submit_button(
                "📥   Register Grievance",
                use_container_width=True,
                type="primary",
            )

        # ── Submit handler ────────────────────────────────────────────────────
        if wi_submit:
            use_ocr = wi_channel == "letter" and wi_image is not None
            if not use_ocr and not wi_text.strip():
                st.error("Please enter a description OR upload a letter / document for OCR extraction.")
            else:
                with st.spinner("Processing grievance — AI is classifying…"):
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
                            st.success(f"✅ Grievance registered — **{data['grievance_id']}**")
                            if use_ocr and data.get("ocr_text"):
                                with st.expander("View extracted OCR text"):
                                    st.text(data["ocr_text"])
                            st.cache_data.clear()
                            st.rerun()
                        else:
                            st.error(f"Error {resp.status_code}: {resp.text}")
                    except Exception as e:
                        st.error(f"Could not reach backend: {e}")
