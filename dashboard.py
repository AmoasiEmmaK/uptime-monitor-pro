"""
Uptime monitor dashboard.

Run with:
    streamlit run dashboard.py

Shows current status of all monitored servers, recent history, and
uptime % over the selected time window. Also lets you add/remove
servers without editing code.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from monitor import (
    init_db,
    add_server,
    DB_PATH,
    get_all_servers_for_user,
    update_server,
    set_server_active,
    delete_server_permanently,
    check_server_now,
)
from auth import (
    init_auth_db, create_user, verify_user, get_user_id, get_user_role,
    is_locked_out, record_login_attempt, record_audit,
)

import os

ASSET_DIR = Path(__file__).resolve().parent / "assets"
LOGO_PATH = ASSET_DIR / "logo.png"
CHECK_METHOD_LABELS = {
    "ping": "Ping (IP/hostname)",
    "http": "HTTP (URL)",
    "api": "API endpoint (HTTP)",
    "tcp": "TCP port (host:port)",
    "dns": "DNS lookup (hostname)",
    "database": "SQLite database",
}
CHECK_METHOD_OPTIONS = list(CHECK_METHOD_LABELS.values())
CHECK_METHOD_CODES = {label: code for code, label in CHECK_METHOD_LABELS.items()}

st.set_page_config(page_title="UptimeMonitor", page_icon=str(LOGO_PATH), layout="wide")
init_db()
init_auth_db()

if "dark_mode" not in st.session_state:
    st.session_state["dark_mode"] = False

st.markdown(
    """
    <style>
    :root {
        --ink: #edf3fa;
        --muted: #a7b6c9;
        --line: #314765;
        --panel: #1c2a3a;
        --canvas: #0e1826;
        --sidebar: #172334;
        --green: #5c8b7a;
        --red: #d77a7d;
        --amber: #d4a64d;
    }
    [data-testid="stAppViewContainer"] { background: var(--canvas); }
    [data-testid="stHeader"] { background: var(--sidebar) !important; border-bottom: 1px solid rgba(255,255,255,.08); overflow: visible !important; }
    [data-testid="stHeader"] *, [data-testid="stHeader"] button,
    [data-testid="stHeader"] [role="button"],
    [data-testid="stHeader"] [data-testid="stMainMenu"],
    [data-testid="stHeader"] [data-testid="stHeaderActionButton"],
    [data-testid="stHeader"] [data-testid="baseButton-headerNoPadding"],
    [data-testid="stHeader"] a, [data-testid="stHeader"] span,
    [data-testid="stHeader"] svg,
    [data-testid="stHeader"] button[title*="Deploy"],
    [data-testid="stHeader"] button[aria-label*="Menu"],
    [data-testid="stHeader"] button[aria-label*="Deploy"] { color: #f8fafc !important; fill: #f8fafc !important; }
    [data-testid="stHeader"] button,
    [data-testid="stHeader"] [role="button"],
    [data-testid="stHeader"] [data-testid="stMainMenu"],
    [data-testid="stHeader"] [data-testid="stHeaderActionButton"] {
        opacity: 1 !important; visibility: visible !important;
        background: rgba(255,255,255,.08) !important;
        border: 1px solid rgba(255,255,255,.12) !important;
        border-radius: 6px !important;
        color: #f8fafc !important;
    }
    [data-testid="stSidebar"] { background: var(--sidebar) !important; border-right: 0; color: #eef4f8 !important; }
    [data-testid="stSidebar"] *, [data-testid="stSidebar"] p, [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] div, [data-testid="stSidebar"] span, [data-testid="stSidebar"] a,
    [data-testid="stSidebar"] li, [data-testid="stSidebar"] strong,
    [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3,
    [data-testid="stSidebar"] h4, [data-testid="stSidebar"] [data-testid="stForm"] *,
    [data-testid="stSidebar"] [data-testid="stWidgetLabel"],
    [data-testid="stSidebar"] [data-testid="stWidgetLabel"] label,
    [data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
    [data-testid="stSidebar"] [data-testid="stForm"] label,
    [data-testid="stSidebar"] [data-testid="stForm"] p,
    [data-testid="stSidebar"] [data-testid="stForm"] div,
    [data-testid="stSidebar"] [data-testid="stForm"] span,
    [data-testid="stSidebar"] [data-testid="stForm"] [data-testid="stWidgetLabel"] { color: #f8fafc !important; }
    [data-testid="stSidebar"] input::placeholder,
    [data-testid="stSidebar"] textarea::placeholder,
    [data-testid="stSidebar"] [data-baseweb="select"] input::placeholder,
    [data-testid="stSidebar"] [data-baseweb="input"] input::placeholder { color: #cbd5e1 !important; }
    [data-testid="stSidebar"] input,
    [data-testid="stSidebar"] textarea,
    [data-testid="stSidebar"] [data-baseweb="input"],
    [data-testid="stSidebar"] [data-baseweb="select"],
    [data-testid="stSidebar"] [data-testid="stTextInput"] > div,
    [data-testid="stSidebar"] [data-testid="stNumberInput"] > div,
    [data-testid="stSidebar"] [data-testid="stSelectbox"] > div { background: #0b1220 !important; color: #f8fafc !important; border: 1px solid #334155 !important; }
    [data-testid="stSidebar"] input,
    [data-testid="stSidebar"] textarea,
    [data-testid="stSidebar"] [data-baseweb="input"] input,
    [data-testid="stSidebar"] [data-baseweb="select"] input,
    [data-testid="stSidebar"] [data-baseweb="input"] div,
    [data-testid="stSidebar"] [data-baseweb="select"] div,
    [data-testid="stSidebar"] [data-baseweb="select"] [role="button"],
    [data-testid="stSidebar"] [data-baseweb="select"] [role="listbox"],
    [data-testid="stSidebar"] [data-testid="stSelectbox"] [data-baseweb="select"] > div,
    [data-testid="stSidebar"] [data-testid="stSelectbox"] [data-baseweb="select"] > div > div,
    [data-testid="stSidebar"] [data-testid="stSelectbox"] [data-baseweb="select"] span,
    [data-testid="stSidebar"] [data-testid="stTextInput"] input,
    [data-testid="stSidebar"] [data-testid="stNumberInput"] input,
    [data-testid="stSidebar"] [data-testid="stSelectbox"] input,
    [data-testid="stSidebar"] [data-testid="stSelectbox"] div,
    [data-testid="stSidebar"] [data-testid="stTextInput"] input:last-of-type,
    [data-testid="stSidebar"] [data-testid="stTextInput"] textarea:last-of-type { color: #f8fafc !important; }
    [data-testid="stSidebar"] hr { border-color: rgba(255,255,255,.14); }
    [data-testid="stTextInput"] input,
    [data-testid="stTextArea"] textarea,
    [data-testid="stNumberInput"] input,
    [data-testid="stSelectbox"] input,
    [data-testid="stSelectbox"] div,
    [data-baseweb="input"],
    [data-baseweb="select"],
    [data-baseweb="textarea"],
    [data-testid="stSelectbox"] [role="button"],
    [data-testid="stSelectbox"] [role="combobox"],
    [data-testid="stSelectbox"] [role="listbox"],
    [data-testid="stSelectbox"] [data-baseweb="select"] *,
    [data-testid="stSelectbox"] [data-baseweb="select"] [role="button"],
    [data-testid="stTextInput"] input:last-of-type,
    [data-testid="stTextInput"] textarea:last-of-type,
    [data-testid="stNumberInput"] input:last-of-type { background: #0b1220 !important; color: #f8fafc !important; border: 1px solid #334155 !important; }
    [data-baseweb="menu"], [data-baseweb="menu"] *,
    [role="listbox"], [role="listbox"] *, [role="option"] {
        background: #0b1220 !important;
        color: #f8fafc !important;
    }
    [data-baseweb="menu"] [role="option"]:hover,
    [role="listbox"] [role="option"]:hover,
    [data-baseweb="menu"] [aria-selected="true"],
    [role="listbox"] [aria-selected="true"] {
        background: #243448 !important;
        color: #ffffff !important;
    }
    [data-testid="stTextInput"] input::placeholder,
    [data-testid="stTextArea"] textarea::placeholder,
    [data-testid="stNumberInput"] input::placeholder,
    [data-testid="stSelectbox"] input::placeholder { color: #cbd5e1 !important; }
    .block-container { max-width: 1480px; padding-top: 2rem; padding-bottom: 4rem; }
    h1, h2, h3 { color: var(--ink); letter-spacing: 0; font-family: "Trebuchet MS", sans-serif; }
    h1 { font-weight: 700; }
    [data-testid="stMetric"] { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 1rem 1.1rem; box-shadow: 0 5px 18px rgba(15, 23, 41, .35); }
    [data-testid="stMetricLabel"] { color: var(--muted); font-size: .78rem; text-transform: uppercase; letter-spacing: .06em; }
    [data-testid="stMetricValue"] { color: var(--ink); }
    [data-testid="stExpander"] { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; margin-bottom: .7rem; }
    .stDataFrame { border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
    .stButton > button, .stFormSubmitButton > button { border-radius: 6px; font-weight: 600; border: 1px solid #465a72; color: #edf3fa !important; background: #243448; }
    .stButton > button *, .stFormSubmitButton > button * { color: inherit !important; }
    .stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] { background: var(--green); border-color: var(--green); color: #f3f7fb !important; }
    .login-mark { color: var(--green); font-size: .78rem; font-weight: 700; letter-spacing: .16em; text-transform: uppercase; }
    [data-testid="stForm"] { max-width: 440px; margin: 0 auto; border-radius: 8px; border: 1px solid var(--line); background: var(--panel); padding: 1rem 1.1rem; }
    [data-testid="stTabs"] { max-width: 480px; margin: 0 auto; }
    [data-testid="stTabs"] [role="tablist"] { justify-content: center; }
    [data-testid="stTabs"] [role="tab"] { flex: 1; text-align: center; }
    [data-testid="stForm"] label { text-align: center; width: 100%; }
    [data-testid="stForm"] input { text-align: center; }
    [data-testid="stForm"] button { width: 100%; justify-content: center; }
    .login-mark, [data-testid="stCaptionContainer"] { text-align: center; }
    .login-panel img { max-height: 92px; object-fit: contain; }
    .page-intro { color: var(--muted); margin-top: -.5rem; margin-bottom: 1.5rem; }
    [data-testid="stAppViewContainer"] p, [data-testid="stAppViewContainer"] label,
    [data-testid="stMarkdownContainer"] { color: #dfeaf8; }
    [data-testid="stAppViewContainer"] h1, [data-testid="stAppViewContainer"] h2,
    [data-testid="stAppViewContainer"] h3, [data-testid="stAppViewContainer"] h4,
    [data-testid="stAppViewContainer"] strong { color: #f8fafc !important; }
    [data-testid="stCaptionContainer"] { color: #475569 !important; opacity: 1 !important; }
    [data-testid="stExpander"] summary, [data-testid="stExpander"] summary * { color: #f8fafc !important; }
    [data-testid="stExpander"] p, [data-testid="stExpander"] span,
    [data-testid="stExpander"] label, [data-testid="stExpander"] strong,
    [data-testid="stDataFrame"] *, [data-testid="stTable"] * { color: #f8fafc !important; }
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"],
    [data-testid="stSidebar"] label, [data-testid="stSidebar"] p { color: #f8fafc !important; }
    [data-testid="stTextInput"] input, [data-testid="stNumberInput"] input,
    [data-testid="stTextArea"] textarea { color: #172235 !important; background: #ffffff !important; }
    [data-testid="stSidebar"] [data-baseweb="select"] *,
    [data-testid="stSidebar"] [data-baseweb="input"] *,
    [data-testid="stSidebar"] [data-baseweb="select"] [role="button"],
    [data-testid="stSidebar"] [data-baseweb="select"] input,
    [data-testid="stSidebar"] [data-baseweb="input"] input,
    [data-testid="stSidebar"] [data-baseweb="select"] div,
    [data-testid="stSidebar"] [data-baseweb="input"] div,
    [data-testid="stSidebar"] [role="option"],
    [data-testid="stSidebar"] [role="listbox"] *,
    [data-testid="stSidebar"] [data-baseweb="menu"] * { color: #f8fafc !important; }
    [data-testid="stSidebar"] [data-baseweb="select"],
    [data-testid="stSidebar"] [data-baseweb="input"],
    [data-testid="stSidebar"] [data-baseweb="menu"],
    [data-testid="stSidebar"] [data-testid="stTextInput"] > div,
    [data-testid="stSidebar"] [data-testid="stNumberInput"] > div { background: #0b1220 !important; color: #f8fafc !important; border: 1px solid #334155 !important; border-radius: 6px; }
    [data-testid="stSidebar"] [data-testid="stWidgetLabel"] label,
    [data-testid="stSidebar"] [data-testid="stForm"] label { color: #f8fafc !important; }
    [data-testid="stSidebar"] [data-baseweb="select"] svg { fill: #172235 !important; }
    [data-testid="stSidebar"] [data-testid="stSelectbox"] [data-baseweb="select"] > div,
    [data-testid="stSidebar"] [data-testid="stSelectbox"] [data-baseweb="select"] > div > div,
    [data-testid="stSidebar"] [data-testid="stSelectbox"] [data-baseweb="select"] span,
    [data-testid="stSidebar"] [data-testid="stSelectbox"] [data-baseweb="select"] input {
        color: #f8fafc !important;
        -webkit-text-fill-color: #f8fafc !important;
    }
    [data-testid="stSidebar"] .stButton > button,
    [data-testid="stSidebar"] .stFormSubmitButton > button,
    [data-testid="stSidebar"] button[kind="primary"] { color: #f8fafc !important; background: #138a63 !important; border-color: #138a63 !important; }
    [data-testid="stSidebar"] .stButton > button *,
    [data-testid="stSidebar"] .stFormSubmitButton > button * { color: inherit !important; }
    .stButton > button, .stFormSubmitButton > button { color: #172235 !important; background: #ffffff; }
    .stDownloadButton > button { color: #172235 !important; background: #ffffff; border: 1px solid #b9c6d4; }
    .stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] { color: #ffffff !important; }
    [data-testid="stAlert"] p { color: inherit !important; }
    .login-logo + [data-testid="stImage"] { display: flex; justify-content: center; width: 100%; }
    .login-logo + [data-testid="stImage"] img { display: block; margin: 0 auto; }
    </style>
    """,
    unsafe_allow_html=True,
)

if st.session_state["dark_mode"]:
    st.markdown(
        """
        <style>
        [data-testid="stAppViewContainer"] { background:#101827; }
        [data-testid="stAppViewContainer"] h1, [data-testid="stAppViewContainer"] h2,
        [data-testid="stAppViewContainer"] h3, [data-testid="stAppViewContainer"] h4,
        [data-testid="stAppViewContainer"] strong { color:#f8fafc !important; }
        [data-testid="stAppViewContainer"] p, [data-testid="stMarkdownContainer"] { color:#dbe5ef !important; }
        [data-testid="stMetric"], [data-testid="stExpander"], [data-testid="stForm"] { background:#1b2638; border-color:#334155; }
        [data-testid="stMetricLabel"], [data-testid="stCaptionContainer"] { color:#cbd5e1 !important; }
        [data-testid="stMetricValue"], [data-testid="stExpander"] summary, [data-testid="stExpander"] summary * { color:#f8fafc !important; }
        [data-testid="stSidebar"] { background:#122033 !important; }
        [data-testid="stSidebar"] *, [data-testid="stSidebar"] p, [data-testid="stSidebar"] label,
        [data-testid="stSidebar"] div, [data-testid="stSidebar"] span, [data-testid="stSidebar"] a,
        [data-testid="stSidebar"] li, [data-testid="stSidebar"] strong { color:#eef4f8 !important; }
        .stButton > button, .stFormSubmitButton > button { color:#f8fafc !important; background:#1e293b !important; border-color:#475569 !important; }
        .stButton > button *, .stFormSubmitButton > button * { color: inherit !important; }
        .stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] { background:#138a63 !important; border-color:#138a63 !important; color:#ffffff !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )
def show_logo(width: int = 160):
    """Show the UptimeMonitor logo if the asset file is present."""
    if LOGO_PATH.exists():
        st.image(LOGO_PATH, width=width)


def show_login_screen():
    """
    Render a login/signup gate. Stops the rest of the app from
    rendering until the user is authenticated.
    """
    _, col2, _ = st.columns([1.5, 1, 1.5])
    with col2:
        st.markdown('<div class="login-logo"></div>', unsafe_allow_html=True)
        show_logo(width=110)
    st.markdown("<h1 style='text-align: center; font-size: 2rem;'>UptimeMonitor</h1>", unsafe_allow_html=True)
    st.markdown(
        "<p style='text-align: center; color: #00E676;'>Monitor • Detect • Stay Online</p>",
        unsafe_allow_html=True,
    )
    st.markdown('<div class="login-mark">Service reliability console</div>', unsafe_allow_html=True)
    st.caption("Sign in to continue")

    login_tab, signup_tab = st.tabs(["Log in", "Create account"])

    with login_tab:
        with st.form("login_form"):
            login_email = st.text_input("Email", key="login_email")
            login_password = st.text_input("Password", type="password", key="login_password")
            login_submitted = st.form_submit_button("Log in")
            if login_submitted:
                locked, minutes_remaining = is_locked_out(login_email)
                if locked:
                    st.error(f"Too many failed attempts. Try again in ~{minutes_remaining} minute(s).")
                else:
                    success, message = verify_user(login_email, login_password)
                    record_login_attempt(login_email, success)
                    if success:
                        st.session_state["logged_in"] = True
                        st.session_state["user_email"] = login_email.strip().lower()
                        st.session_state["user_id"] = get_user_id(login_email)
                        record_audit(st.session_state["user_id"], "login_success", login_email.strip().lower())
                        st.rerun()
                    else:
                        st.error(message)

    with signup_tab:
        with st.form("signup_form"):
            signup_email = st.text_input("Email", key="signup_email")
            signup_password = st.text_input("Password (min 8 characters)", type="password", key="signup_password")
            signup_submitted = st.form_submit_button("Create account")
            if signup_submitted:
                success, message = create_user(signup_email, signup_password)
                if success:
                    st.success(f"{message} You can log in now.")
                else:
                    st.error(message)


# --- Auth gate: nothing below this runs until the user is logged in ---
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False

if not st.session_state["logged_in"]:
    show_login_screen()
    st.stop()

CURRENT_USER_ID = st.session_state.get("user_id")

header_col1, header_col2 = st.columns([1, 8])
with header_col1:
    show_logo(width=70)
with header_col2:
    st.markdown("<h1 style='margin-bottom:0;'>Uptime<span style='color:#138a63;'>Monitor</span></h1>", unsafe_allow_html=True)
    st.markdown('<div class="page-intro">A clear view of service health, response time, and incidents.</div>', unsafe_allow_html=True)

action_col1, action_col2 = st.columns([1.35, 8.65])
with action_col1:
    if st.button("Refresh now", use_container_width=True):
        st.rerun()

with st.sidebar:
    show_logo(width=100)
    st.caption(f"Logged in as {st.session_state.get('user_email', '')}")
    st.caption(f"Role: {get_user_role(CURRENT_USER_ID) or 'user'}")
    auto_refresh = st.selectbox(
        "Auto-refresh",
        options=[0, 30, 60, 300],
        format_func=lambda seconds: "Off" if seconds == 0 else f"Every {seconds} seconds",
        help="Refresh dashboard data automatically while this page is open.",
    )
    if auto_refresh:
        st_autorefresh(interval=auto_refresh * 1000, key="dashboard_auto_refresh")
    if st.button("Refresh data", use_container_width=True):
        st.rerun()
    if st.button("Check all services", use_container_width=True):
        active_servers = [server for server in get_all_servers_for_user(CURRENT_USER_ID) if server[4]]
        if not active_servers:
            st.info("No active services to check.")
        else:
            with st.spinner(f"Checking {len(active_servers)} service(s)..."):
                for server in active_servers:
                    check_server_now(server[0], CURRENT_USER_ID)
            st.success(f"Checked {len(active_servers)} service(s).")
            st.rerun()
    if st.button("Log out"):
        st.session_state["logged_in"] = False
        st.session_state.pop("user_email", None)
        st.session_state.pop("user_id", None)
        st.rerun()

def get_connection():
    return sqlite3.connect(DB_PATH)


def load_servers():
    conn = get_connection()
    df = pd.read_sql_query("SELECT * FROM servers", conn)
    conn.close()
    return df


def load_latest_status(user_id: int):
    """Get the most recent check for each of this user's servers."""
    conn = get_connection()
    query = """
        SELECT s.id, s.name, s.address, s.check_type, s.maintenance_mode, c.timestamp, c.is_up, c.response_time_ms
        FROM servers s
        LEFT JOIN checks c ON c.id = (
            SELECT id FROM checks
            WHERE server_id = s.id
            ORDER BY timestamp DESC
            LIMIT 1
        )
        WHERE s.active = 1 AND s.user_id = ?
    """
    df = pd.read_sql_query(query, conn, params=(user_id,))
    conn.close()
    return df


def load_history(server_id: int, hours: int = 24):
    conn = get_connection()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    df = pd.read_sql_query(
        "SELECT * FROM checks WHERE server_id = ? AND timestamp >= ? ORDER BY timestamp",
        conn,
        params=(server_id, cutoff),
    )
    conn.close()
    return df


def load_recent_checks(user_id: int, limit: int = 25):
    conn = get_connection()
    df = pd.read_sql_query(
        """SELECT s.name, s.check_type, c.timestamp, c.is_up, c.response_time_ms
           FROM checks c JOIN servers s ON s.id = c.server_id
           WHERE s.user_id = ? ORDER BY c.timestamp DESC LIMIT ?""",
        conn, params=(user_id, limit),
    )
    conn.close()
    return df


def load_incidents(user_id: int, limit: int = 50):
    """Load recent incidents across this user's servers, most recent first."""
    conn = get_connection()
    query = """
        SELECT i.id, s.name, s.address, i.started_at, i.ended_at, i.note
        FROM incidents i
        JOIN servers s ON s.id = i.server_id
        WHERE s.user_id = ?
        ORDER BY i.started_at DESC
        LIMIT ?
    """
    df = pd.read_sql_query(query, conn, params=(user_id, limit))
    conn.close()
    return df


def format_duration(start_iso: str, end_iso: str | None) -> str:
    """Format the gap between two ISO timestamps as e.g. '3m 21s', or 'Ongoing' if still open."""
    if end_iso is None or pd.isna(end_iso):
        return "Ongoing"
    start = pd.to_datetime(start_iso)
    end = pd.to_datetime(end_iso)
    seconds = int((end - start).total_seconds())
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def uptime_percent(df: pd.DataFrame) -> float:
    if df.empty:
        return 0.0
    return round(100 * df["is_up"].sum() / len(df), 1)


# --- Sidebar: add a server ---
with st.sidebar:
    st.header("Add a server")
    st.caption("Add a server or service to monitor.")
    with st.form("add_server_form", clear_on_submit=True):
        new_name = st.text_input("Server name", placeholder="e.g. Branch Office Router")
        new_check_method = st.selectbox(
            "Check method",
            options=CHECK_METHOD_OPTIONS,
            index=0,
            key="add_check_method_label",
        )
        new_check_type = CHECK_METHOD_CODES[new_check_method]
        placeholder = {
            "ping": "e.g. 192.168.1.1",
            "http": "e.g. https://intranet.inlaks.com",
            "api": "e.g. https://api.example.com/health",
            "tcp": "e.g. 192.168.1.10:3306",
            "dns": "e.g. intranet.inlaks.com",
            "database": "e.g. C:/data/app.db",
        }[new_check_type]
        new_address = st.text_input("Target address", placeholder=placeholder)
        new_threshold = st.number_input(
            "Alert after consecutive failures",
            min_value=1, max_value=10, value=1, step=1,
            help="1 = alert immediately on the first failure (fastest, most false positives). "
                 "Higher values (e.g. 3) wait for that many consecutive failures before "
                 "alerting, filtering out brief network blips at the cost of slower detection.",
        )
        submitted = st.form_submit_button("Add server")
        if submitted:
            clean_name = new_name.strip()
            clean_address = new_address.strip()
            if not clean_name or not clean_address:
                st.warning("Enter a server name and target address.")
            else:
                add_server(clean_name, clean_address, new_check_type, CURRENT_USER_ID, int(new_threshold))
                st.success(f"Added {clean_name} to monitoring.")
                st.rerun()

    st.divider()
    st.header("Manage monitored servers")
    all_servers = get_all_servers_for_user(CURRENT_USER_ID)
    if not all_servers:
        st.caption("No servers added yet.")
    else:
        server_labels = {
            sid: f"{name} ({'active' if active else 'inactive'})"
            for sid, name, _, _, active, _, _ in all_servers
        }
        selected_id = st.selectbox(
            "Server to manage",
            options=list(server_labels.keys()),
            format_func=lambda sid: server_labels[sid],
        )
        selected = next(s for s in all_servers if s[0] == selected_id)
        _, sel_name, sel_address, sel_check_type, sel_active, sel_threshold, sel_maintenance = selected

        if st.button("Check now", use_container_width=True):
            with st.spinner(f"Checking {sel_name}..."):
                manual_result = check_server_now(selected_id, CURRENT_USER_ID)
            if manual_result and manual_result[0]:
                st.success(f"{sel_name} is UP ({manual_result[1]} ms)")
            else:
                st.error(f"{sel_name} is DOWN")

        with st.form("edit_server_form"):
            edit_name = st.text_input("Server name", value=sel_name)
            edit_check_method = st.selectbox(
                "Check method",
                options=CHECK_METHOD_OPTIONS,
                index=CHECK_METHOD_OPTIONS.index(CHECK_METHOD_LABELS.get(sel_check_type, CHECK_METHOD_OPTIONS[0])),
                key=f"edit_check_method_label_v2_{selected_id}",
            )
            edit_check_type = CHECK_METHOD_CODES[edit_check_method]
            edit_address = st.text_input("Target address", value=sel_address)
            edit_threshold = st.number_input(
                "Alert after consecutive failures",
                min_value=1, max_value=10, value=sel_threshold, step=1,
            )
            edit_maintenance = st.checkbox(
                "Maintenance mode (suppress alerts and incidents)",
                value=bool(sel_maintenance),
            )
            save_clicked = st.form_submit_button("Save changes")
            if save_clicked:
                update_server(
                    selected_id, edit_name.strip(), edit_address.strip(),
                    edit_check_type, CURRENT_USER_ID, int(edit_threshold), edit_maintenance,
                )
                st.success("Saved.")
                st.rerun()

        col_a, col_b = st.columns(2)
        with col_a:
            if sel_active:
                if st.button("Deactivate", use_container_width=True):
                    set_server_active(selected_id, False, CURRENT_USER_ID)
                    st.success(f"Deactivated {sel_name}. History is kept.")
                    st.rerun()
            else:
                if st.button("Reactivate", use_container_width=True):
                    set_server_active(selected_id, True, CURRENT_USER_ID)
                    st.success(f"Reactivated {sel_name}.")
                    st.rerun()
        with col_b:
            confirm_delete = st.checkbox("Confirm permanent delete", key=f"confirm_del_{selected_id}")
            if st.button("Delete permanently", type="primary", use_container_width=True, disabled=not confirm_delete):
                delete_server_permanently(selected_id, CURRENT_USER_ID)
                st.success(f"Deleted {sel_name} and its history.")
                st.rerun()
        st.caption("Deactivate keeps history and can be undone. Delete permanently "
                   "removes the server AND all its check/incident history — cannot be undone.")

    st.divider()
    hours_window = st.selectbox(
        "History window (for charts below)",
        options=[1, 6, 24, 72, 168, 720],
        format_func=lambda h: (
            f"Last {h}h" if h < 24 else
            "Last 24 hours" if h == 24 else
            "Last 7 days" if h == 168 else
            "Last 30 days" if h == 720 else
            f"Last {h}h"
        ),
        index=2,
    )
    st.caption("Note: the monitor.py script must be running separately "
               "for new checks to be logged.")


# --- Main: current status ---
status_df = load_latest_status(CURRENT_USER_ID)

check_times = pd.to_datetime(status_df["timestamp"], utc=True, errors="coerce").dropna()
if not check_times.empty:
    minutes_since_check = (pd.Timestamp.now(tz="UTC") - check_times.max()).total_seconds() / 60
    if minutes_since_check > 3:
        st.warning(
            f"Monitor data is {minutes_since_check:.0f} minutes old. "
            "Check that monitor.py is still running.",
            icon="⏱️",
        )

with st.sidebar:
    st.divider()
    st.header("Focus view")
    service_query = st.text_input("Search services", placeholder="Name or address")
    state_filter = st.selectbox("Show", ["All", "Up", "Degraded", "Down", "No data"])

if service_query:
    search_text = service_query.strip().lower()
    status_df = status_df[
        status_df["name"].str.lower().str.contains(search_text, na=False)
        | status_df["address"].str.lower().str.contains(search_text, na=False)
    ]
if state_filter == "Up":
    status_df = status_df[status_df["is_up"] == 1]
elif state_filter == "Down":
    status_df = status_df[status_df["is_up"] == 0]
elif state_filter == "No data":
    status_df = status_df[status_df["is_up"].isna()]
elif state_filter == "Degraded":
    status_df = status_df[(status_df["is_up"] == 1) & (status_df["response_time_ms"] >= 1000)]

if status_df.empty:
    st.info("No servers configured yet. Add one from the sidebar.")
else:
    # --- DOWN banner: show prominently at the top if anything is down ---
    maintenance_df = status_df[status_df["maintenance_mode"] == 1]
    down_df = status_df[(status_df["is_up"] == 0) & (status_df["maintenance_mode"] == 0)]
    degraded_df = status_df[(status_df["is_up"] == 1) & (status_df["response_time_ms"] >= 1000) & (status_df["maintenance_mode"] == 0)]
    if not down_df.empty:
        down_names = ", ".join(down_df["name"].tolist())
        st.error(f"🔴 {len(down_df)} server(s) currently DOWN: {down_names}", icon="🚨")
    elif not degraded_df.empty:
        st.warning(f"🟠 {len(degraded_df)} service(s) are responding slowly", icon="⚠️")
    else:
        checked_df = status_df.dropna(subset=["is_up"])
        if not checked_df.empty:
            st.success("🟢 All monitored servers are currently UP", icon="✅")

    # --- At-a-glance summary ---
    total_servers = len(status_df)
    up_count = int((status_df["is_up"] == 1).sum())
    down_count = int((status_df["is_up"] == 0).sum())
    degraded_count = len(degraded_df)
    maintenance_count = len(maintenance_df)
    no_data_count = int(status_df["is_up"].isna().sum())

    # Overall uptime % across all servers, over the selected window
    all_history = pd.concat(
        [load_history(row["id"], hours=hours_window) for _, row in status_df.iterrows()],
        ignore_index=True,
    ) if total_servers else pd.DataFrame(columns=["is_up"])
    overall_uptime = uptime_percent(all_history)

    summary_cols = st.columns(6)
    summary_cols[0].metric("Servers monitored", total_servers)
    summary_cols[1].metric("Up", up_count)
    summary_cols[2].metric("Degraded", degraded_count)
    summary_cols[3].metric("Down", down_count)
    summary_cols[4].metric(f"Overall uptime ({hours_window}h)", f"{overall_uptime}%")
    summary_cols[5].metric("Maintenance", maintenance_count)
    if no_data_count:
        st.caption(f"{no_data_count} server(s) have no data yet — waiting on monitor.py's next check cycle.")

    st.divider()
    st.subheader("📊 Analytics")
    st.caption("Uptime percentage over standard reporting windows.")

    analytics_rows = []
    for _, row in status_df.iterrows():
        history_24h = load_history(row["id"], hours=24)
        uptime_24h = uptime_percent(history_24h)
        uptime_7d = uptime_percent(load_history(row["id"], hours=24 * 7))
        uptime_30d = uptime_percent(load_history(row["id"], hours=24 * 30))
        analytics_rows.append({
            "Server": row["name"],
            "24-hour uptime": f"{uptime_24h}%",
            "7-day uptime": f"{uptime_7d}%",
            "30-day uptime": f"{uptime_30d}%",
            "Failed checks (24h)": int((history_24h["is_up"] == 0).sum()),
            "Avg response (24h)": f"{history_24h['response_time_ms'].dropna().mean():.1f} ms" if not history_24h.empty else "-",
        })
    st.dataframe(pd.DataFrame(analytics_rows), use_container_width=True, hide_index=True)
    st.caption("Note: 7-day and 30-day figures will only be fully accurate once the monitor "
               "has actually been running that long — early on, they reflect however much "
               "history exists so far.")

    st.divider()
    st.subheader("Recent checks")
    recent_checks = load_recent_checks(CURRENT_USER_ID)
    if recent_checks.empty:
        st.caption("No checks recorded yet.")
    else:
        recent_checks["timestamp"] = pd.to_datetime(recent_checks["timestamp"]).dt.strftime("%Y-%m-%d %H:%M:%S")
        recent_checks["status"] = recent_checks["is_up"].map({1: "UP", 0: "DOWN"})
        st.dataframe(recent_checks[["timestamp", "name", "check_type", "status", "response_time_ms"]], use_container_width=True, hide_index=True)
        st.download_button("Download recent checks", recent_checks.to_csv(index=False), "recent-checks.csv", "text/csv")

    st.divider()
    st.subheader("Current status")
    cols = st.columns(min(len(status_df), 4) or 1)

    for i, row in status_df.iterrows():
        col = cols[i % len(cols)]
        with col:
            if row["maintenance_mode"] == 1:
                st.metric(row["name"], "🛠 MAINTENANCE")
            elif pd.isna(row["is_up"]):
                st.metric(row["name"], "No data yet")
            elif row["is_up"] == 1:
                status_label = "🟠 SLOW" if row["response_time_ms"] >= 1000 else "🟢 UP"
                st.metric(row["name"], status_label, f"{row['response_time_ms']} ms")
            else:
                st.metric(row["name"], "🔴 DOWN")
            type_label = {"http": "🌐 HTTP", "api": "⚙️ API", "tcp": "🔌 TCP", "dns": "🧭 DNS", "database": "🗄️ Database"}.get(row["check_type"], "📡 Ping")
            st.caption(f"{row['address']} · {type_label}")
            if pd.isna(row["timestamp"]):
                st.caption("Last check: never")
            else:
                checked_at = pd.to_datetime(row["timestamp"], utc=True).strftime("%Y-%m-%d %H:%M:%S UTC")
                st.caption(f"Last check: {checked_at}")

    st.divider()
    st.subheader("History & uptime %")

    for _, row in status_df.iterrows():
        history = load_history(row["id"], hours=hours_window)
        uptime = uptime_percent(history)

        with st.expander(f"{row['name']} ({row['address']}) — {uptime}% uptime"):
            if history.empty:
                st.write("No checks logged yet in this window.")
            else:
                history["timestamp"] = pd.to_datetime(history["timestamp"])
                chart_df = history.set_index("timestamp")[["is_up"]]
                st.line_chart(chart_df, height=150)
                response_df = history.set_index("timestamp")[["response_time_ms"]].dropna()
                if not response_df.empty:
                    st.caption("Response time")
                    st.line_chart(response_df, height=150)

                display_df = history[["timestamp", "is_up", "response_time_ms"]].copy()
                display_df["status"] = display_df["is_up"].map({1: "UP", 0: "DOWN"})
                display_df = display_df.drop(columns=["is_up"]).sort_values(
                    "timestamp", ascending=False
                )
                st.dataframe(display_df, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Incident history")

    incidents_df = load_incidents(CURRENT_USER_ID, limit=50)
    if incidents_df.empty:
        st.write("No incidents recorded yet — that's a good sign.")
    else:
        table_rows = []
        for _, row in incidents_df.iterrows():
            ended_at = row["ended_at"]
            note = row["note"]
            event_label = "🔴 Down" if pd.isna(ended_at) or ended_at is None else "🔴 Down → 🟢 Recovered"
            duration_label = format_duration(row["started_at"], ended_at)
            if pd.notna(note) and str(note).strip():
                duration_label += " ⚠️"
            table_rows.append({
                "Server": row["name"],
                "Address": row["address"],
                "Event": event_label,
                "Started": pd.to_datetime(row["started_at"]).strftime("%Y-%m-%d %H:%M:%S"),
                "Duration": duration_label,
                "Note": "" if pd.isna(note) else str(note),
            })
        st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)
        incident_report = pd.DataFrame(table_rows).to_csv(index=False).encode("utf-8-sig")
        st.download_button("Download incident report", incident_report, "incidents.csv", "text/csv")
        st.caption("Most recent incidents first, across all monitored servers. "
                   "⚠️ marks durations that may be inaccurate due to a monitoring gap "
                   "(e.g. laptop sleep) — see the Note column.")
