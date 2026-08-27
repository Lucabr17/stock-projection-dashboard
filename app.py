"""
Stock Price Projection Dashboard
=================================
Run with:
    streamlit run stock_projection_dashboard.py

Deploying (GitHub + Streamlit Community Cloud): commit requirements.txt and
.streamlit/config.toml (both provided alongside this file) to the same repo.
Without requirements.txt, Streamlit Cloud resolves its own package versions,
which can silently differ from what this file was built and tested against.
Without config.toml, the app follows the *viewer's* browser/OS light-or-dark
preference rather than this app's own light-mode design.

A note on live data on a cloud host: Yahoo Finance rate-limits requests from
shared cloud IP ranges (Streamlit Cloud, AWS, etc.) more aggressively than
from a home connection, so "Too Many Requests" errors are common and not a
bug in this file. Every fetched field (current price, EPS TTM, current P/E)
is an editable input specifically so the tool stays fully usable by typing
values in yourself when that happens — see fetch_quote()'s docstring below.

Requirements: see requirements.txt (streamlit, yfinance, pandas, plotly).

Optional — lets Saved Projections sync across devices/restarts via Google Sheets
(otherwise everything falls back to a local SQLite file next to this script):
    pip install gspread google-auth
    # then in .streamlit/secrets.toml:
    #   spreadsheet_id = "..."
    #   [gcp_service_account]
    #   ...your service account JSON fields...
    # Saves go to the first sheet/tab of that spreadsheet (gspread's sheet1).

Data source: Yahoo Finance via yfinance. Educational tool, not investment advice.
"""

import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import yfinance as yf

try:
    import gspread
    from google.oauth2.service_account import Credentials
except Exception:
    gspread = None
    Credentials = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "projections.db"
DEFAULT_GROWTH = 12.0
DEFAULT_PE = 18.0
TARGET_CAGR = 0.15  # Luke's personal target return, used for the buy-price/margin-of-safety line under the chart
SAVE_COLUMNS = [
    "saved_at", "ticker", "company", "current_price", "years",
    "starting_eps", "growth_rate", "future_pe",
    "projected_eps", "projected_price", "annual_return", "total_return",
    "pe_high",
]

INK = "#12141C"
MUTED = "#667085"
GREEN = "#15803D"
GREEN_BRIGHT = "#16A34A"
RED = "#B91C1C"
GLOW = "rgba(22,163,74,0.45)"

st.set_page_config(
    page_title="Stock Projection Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Styling
# Structural grouping (borders/shadows/padding) always comes from Streamlit's
# own st.container(border=True) so the app never depends on hand-rolled HTML
# spanning multiple calls — that's what caused the overlapping/broken cards
# in the previous version. Custom colors/fonts layer on top via each
# container's automatic `.st-key-<key>` class, which is optional polish, not
# something the layout depends on to look correct.
# ---------------------------------------------------------------------------
st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@600;700&family=Inter:wght@400;500;600;700&display=swap');

html, body, [class*="css"], .stApp {{ font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; }}
.block-container {{ max-width:1180px; padding-top:1.6rem; padding-bottom:3rem; }}

.st-key-hero-card {{
    background:linear-gradient(150deg,#EEF1F6 0%, #FBFBFC 65%) !important;
    border:1px solid #e7e9ee !important;
    border-radius:20px !important;
}}
.hero-name {{ font-family:'Space Grotesk',sans-serif; font-weight:700; font-size:19px; letter-spacing:-0.01em; }}
.hero-ticker {{ font-size:12px; font-weight:600; color:{MUTED}; background:rgba(18,20,28,0.06); padding:2px 8px; border-radius:6px; margin-left:8px; }}
.hero-price {{ font-family:'Space Grotesk',sans-serif; font-weight:700; font-size:30px; color:{GREEN_BRIGHT}; text-shadow:0 0 14px {GLOW}; }}
.hero-status {{ font-size:12.5px; font-weight:500; margin-top:2px; }}
.hero-status.live {{ color:{GREEN}; }}
.hero-status.fallback {{ color:{MUTED}; }}

.st-key-metrics-card {{ border-left:3px solid #9aa1ac !important; }}
.st-key-inputs-card {{ border-left:3px solid {GREEN_BRIGHT} !important; }}

[data-testid="stMetricValue"] {{ font-family:'Space Grotesk',sans-serif; font-weight:700; }}
[data-testid="stMetricLabel"] {{ color:{MUTED}; }}

.result-label {{ font-size:12.5px; color:{MUTED}; font-weight:500; margin-bottom:6px; display:block; }}
.result-value {{ font-family:'Space Grotesk',sans-serif; font-weight:700; font-size:23px; display:block; }}
.result-value.positive {{ color:{GREEN}; text-shadow:0 0 12px {GLOW}; }}
.result-value.negative {{ color:{RED}; }}
.st-key-result-total-pos {{ background:{GREEN} !important; border-color:{GREEN} !important; }}
.st-key-result-total-pos .result-label {{ color:rgba(255,255,255,0.75) !important; }}
.st-key-result-total-pos .result-value {{ color:#fff !important; }}
.st-key-result-total-neg {{ background:{RED} !important; border-color:{RED} !important; }}
.st-key-result-total-neg .result-label {{ color:rgba(255,255,255,0.75) !important; }}
.st-key-result-total-neg .result-value {{ color:#fff !important; }}

button[kind="primary"] {{ background:{INK} !important; border-color:{INK} !important; border-radius:10px !important; font-weight:600 !important; }}
button[kind="primary"]:hover {{ background:#282b3a !important; border-color:#282b3a !important; }}

.saved-sub {{ color:#9aa1ac; font-size:12px; }}
.small-note {{ color:{MUTED}; font-size:12.5px; }}
.margin-strip {{
    margin-top:16px; padding:13px 16px; border-radius:12px;
    font-size:13.5px; line-height:1.55; color:{INK}; font-weight:400;
}}
.margin-strip.positive {{ background:#ECFDF3; }}
.margin-strip.caution {{ background:#FEF3C7; }}
.margin-strip.neutral {{ background:#F3F4F6; }}
.margin-strip strong {{ font-family:'Space Grotesk',sans-serif; font-weight:700; }}

@media (max-width: 640px) {{
    .block-container {{ padding-left:0.8rem; padding-right:0.8rem; padding-top:1rem; }}
    .hero-name {{ font-size:16px; }}
    .hero-price {{ font-size:23px; }}
    .result-value {{ font-size:19px; }}
    [data-testid="stMetricValue"] {{ font-size:1.35rem; }}
}}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Persistence — SQLite (always available) + optional Google Sheets
# ---------------------------------------------------------------------------
def _db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS projections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        saved_at TEXT NOT NULL,
        ticker TEXT NOT NULL,
        company TEXT,
        current_price REAL,
        years INTEGER,
        starting_eps REAL,
        growth_rate REAL,
        future_pe REAL,
        pe_high REAL,
        projected_eps REAL,
        projected_price REAL,
        annual_return REAL,
        total_return REAL
    )""")
    # Migrate databases saved before the P/E range feature existed: add the
    # missing column rather than lose (or crash on) older saved projections.
    cols = [r[1] for r in con.execute("PRAGMA table_info(projections)").fetchall()]
    if "pe_high" not in cols:
        con.execute("ALTER TABLE projections ADD COLUMN pe_high REAL")
    con.commit()
    return con


def _local_all():
    con = _db()
    df = pd.read_sql_query("SELECT * FROM projections ORDER BY id DESC", con)
    con.close()
    return df


def _local_save(row):
    con = _db()
    con.execute(
        f"""INSERT INTO projections ({",".join(SAVE_COLUMNS)})
            VALUES ({",".join(["?"] * len(SAVE_COLUMNS))})""",
        row,
    )
    con.commit()
    con.close()


def _local_delete(saved_at):
    con = _db()
    con.execute("DELETE FROM projections WHERE saved_at = ?", (saved_at,))
    con.commit()
    con.close()


def _google_ready():
    # st.secrets raises (rather than returning False) when no secrets.toml
    # exists at all, which is the normal case until Google Sheets is
    # configured — that must not crash the app, it should just mean "use
    # local SQLite instead".
    try:
        return (
            gspread is not None
            and "gcp_service_account" in st.secrets
            and "spreadsheet_id" in st.secrets
        )
    except Exception:
        return False


def _google_ws():
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(dict(st.secrets["gcp_service_account"]), scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(st.secrets["spreadsheet_id"])
    return sh.sheet1


def _google_fix_header(ws, values):
    """Make row 1 match the current SAVE_COLUMNS, replacing it in place
    rather than inserting a new row above it (inserting is what originally
    pushed an old header down into the data and caused it to be misread as
    a corrupted record)."""
    if not values:
        ws.append_row(SAVE_COLUMNS)
        return
    if values[0] != SAVE_COLUMNS:
        end_cell = gspread.utils.rowcol_to_a1(1, len(SAVE_COLUMNS))
        ws.update(values=[SAVE_COLUMNS], range_name=f"A1:{end_cell}")


def _google_all():
    ws = _google_ws()
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame(columns=SAVE_COLUMNS)
    header, data_rows = values[0], values[1:]

    # A leftover header row from an earlier schema change (e.g. the update
    # that added pe_high) reads back as a corrupted "record" — its own
    # cells are literally the column names, which is exactly what crashed
    # on int(float(row["years"])) → could not convert 'years' to float.
    # Detect any such row by its first cell and remove it from the sheet
    # for good, rather than just skip over it every time.
    stray_rows = [i + 2 for i, r in enumerate(data_rows) if r and r[0] == "saved_at"]
    for row_num in reversed(stray_rows):  # bottom-up so earlier indices stay valid
        ws.delete_rows(row_num)
    if stray_rows:
        data_rows = [r for r in data_rows if not (r and r[0] == "saved_at")]

    # Fix the header itself on read (not only on the next save) so rows
    # saved under an older column order display correctly from the very
    # first load after this update.
    if header != SAVE_COLUMNS:
        _google_fix_header(ws, [header] + data_rows)
        header = SAVE_COLUMNS

    records = [{header[i]: (r[i] if i < len(r) else "") for i in range(len(header))} for r in data_rows]
    return pd.DataFrame(records) if records else pd.DataFrame(columns=SAVE_COLUMNS)


def _google_save(row):
    ws = _google_ws()
    values = ws.get_all_values()
    _google_fix_header(ws, values)
    ws.append_row([str(x) for x in row])


def _google_delete(saved_at):
    ws = _google_ws()
    values = ws.get_all_values()
    for idx, r in enumerate(values, start=1):
        if r and r[0] == saved_at:
            ws.delete_rows(idx)
            return


def saved_all():
    if _google_ready():
        try:
            return _google_all(), True
        except Exception as e:
            st.sidebar.warning(f"Google Sheets unavailable, showing local saves instead: {e}")
    return _local_all(), False


def save_projection(row):
    if _google_ready():
        try:
            _google_save(row)
            return
        except Exception as e:
            st.sidebar.warning(f"Could not save to Google Sheets, saved locally instead: {e}")
    _local_save(row)


def delete_projection(saved_at, was_google):
    try:
        if was_google:
            _google_delete(saved_at)
        else:
            _local_delete(saved_at)
    except Exception as e:
        st.sidebar.warning(f"Delete failed: {e}")


# ---------------------------------------------------------------------------
# Yahoo Finance access (cached so slider/input tweaks don't re-hit the network
# on every rerun — only a ticker change or cache expiry triggers a new call)
# ---------------------------------------------------------------------------
def _safe_float(v):
    try:
        if v is None or pd.isna(v):
            return None
        return float(v)
    except Exception:
        return None


def _friendly_fetch_error(exc: Exception) -> str:
    """Yahoo Finance rate-limits shared cloud IPs (Streamlit Cloud, AWS, etc.)
    much harder than a home connection, so 'Too Many Requests' is common on
    a deployed app and isn't a bug here. Translate the raw exception into
    something a user can actually act on instead of showing a stack-trace
    style message as the status text."""
    msg = str(exc)
    if re.search(r"429|too many requests|rate.?limit", msg, re.IGNORECASE):
        return "Yahoo is rate-limiting this server right now"
    return "Live fetch failed"


def _yf_session():
    # A missing/generic User-Agent is one of the easiest "this is a bot"
    # signals a server can check — sending one that looks like an ordinary
    # browser is standard, legitimate practice for any HTTP client, not a
    # way of evading a rate limit once one is actually in effect.
    s = requests.Session()
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    })
    return s


def _quote_cache_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS quote_cache (
        ticker TEXT PRIMARY KEY, fetched_at TEXT NOT NULL,
        company TEXT, price REAL, eps REAL, pe REAL
    )""")
    con.commit()
    return con


def _quote_cache_get(ticker):
    con = _quote_cache_db()
    row = con.execute(
        "SELECT fetched_at, company, price, eps, pe FROM quote_cache WHERE ticker=?", (ticker,)
    ).fetchone()
    con.close()
    if not row:
        return None
    return {"fetched_at": row[0], "company": row[1], "price": row[2], "eps": row[3], "pe": row[4]}


def _quote_cache_set(ticker, q):
    con = _quote_cache_db()
    con.execute(
        """INSERT INTO quote_cache (ticker, fetched_at, company, price, eps, pe) VALUES (?,?,?,?,?,?)
           ON CONFLICT(ticker) DO UPDATE SET
             fetched_at=excluded.fetched_at, company=excluded.company,
             price=excluded.price, eps=excluded.eps, pe=excluded.pe""",
        (ticker, datetime.now().isoformat(timespec="seconds"), q["company"], q["price"], q["eps"], q["pe"]),
    )
    con.commit()
    con.close()


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_quote(ticker: str):
    last_exc = None
    for attempt in range(2):  # one retry — a real rate-limit ban needs minutes to clear,
        try:                  # not milliseconds, so more attempts than this wouldn't help
            t = yf.Ticker(ticker, session=_yf_session())
            info = t.info or {}
            price = _safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
            if price is None:
                hist = t.history(period="5d")
                if not hist.empty:
                    price = _safe_float(hist["Close"].dropna().iloc[-1])
            return {
                "ticker": ticker.upper(),
                "company": info.get("longName") or info.get("shortName") or ticker.upper(),
                "price": price,
                "eps": _safe_float(info.get("trailingEps")),
                "pe": _safe_float(info.get("trailingPE")),
            }
        except Exception as e:
            last_exc = e
            if attempt == 0:
                time.sleep(1.5)
    raise last_exc


def _annual_series(ticker, row_candidates):
    try:
        t = yf.Ticker(ticker, session=_yf_session())
        inc = t.income_stmt
        if inc is None or inc.empty:
            return None
        for row in row_candidates:
            if row in inc.index:
                s = pd.to_numeric(inc.loc[row], errors="coerce").dropna()
                if not s.empty:
                    s.index = pd.to_datetime(s.index)
                    return s.sort_index()
    except Exception:
        pass
    return None


def _cagr(series, years):
    if series is None or len(series) < years + 1:
        return None
    s = series.sort_index()
    end, start = s.iloc[-1], s.iloc[-(years + 1)]
    if start <= 0 or end <= 0:
        return None
    return (end / start) ** (1 / years) - 1


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_historical_cagr(ticker: str):
    rev = _annual_series(ticker, ["Total Revenue", "Operating Revenue"])
    eps = _annual_series(ticker, ["Diluted EPS", "Basic EPS"])
    return {
        "rev": {y: _cagr(rev, y) for y in (1, 3, 4)},
        "eps": {y: _cagr(eps, y) for y in (1, 3, 4)},
    }


@st.cache_data(ttl=600, show_spinner=False)
def search_tickers(query: str):
    try:
        res = yf.Search(query, max_results=8)
        quotes = getattr(res, "quotes", [])
        out = []
        for x in quotes:
            symbol = x.get("symbol")
            name = x.get("longname") or x.get("shortname") or symbol
            if symbol:
                out.append((symbol, name))
        return out
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Calculations
# ---------------------------------------------------------------------------
def project(current_price, starting_eps, growth_pct, future_pe, years):
    g = growth_pct / 100.0
    p_eps = starting_eps * ((1 + g) ** years)
    p_price = p_eps * future_pe
    annual = total = None
    if current_price and current_price > 0 and years > 0:
        ratio = p_price / current_price
        total = ratio - 1
        if ratio > 0:
            annual = ratio ** (1.0 / years) - 1
    return p_eps, p_price, annual, total


def build_trajectory(current_price, annual_return, years):
    """Smooth compounding from today's price at the computed annual CAGR —
    this is what makes the chart's year-by-year badges consistent with the
    Annual Return figure shown above it, rather than re-rating every year's
    EPS at the future P/E immediately (which front-loads the whole return
    into year one and no longer matches the Annual Return metric)."""
    if not current_price or current_price <= 0 or annual_return is None:
        return [0.0] * (years + 1)
    return [current_price * ((1 + annual_return) ** i) for i in range(years + 1)]


def build_labels(years):
    now = datetime.now()
    q = (now.month - 1) // 3 + 1
    return [f"Q{q} {now.year + i}" for i in range(years + 1)]


def fmt_money(v):
    return "—" if v is None else f"${v:,.2f}"


def fmt_pct(v):
    return "—" if v is None else f"{v * 100:+.1f}%"


def _default_kw(key, default_value):
    """Pass value= only on a widget's first render under this key. Once
    load_saved_row (or the user) has seeded st.session_state[key], passing
    value= again alongside it is a Streamlit anti-pattern that logs a
    warning now and may hard-error in a future version."""
    return {} if key in st.session_state else {"value": default_value}


def fmt_money_range(lo, hi):
    if lo is None or hi is None:
        return "—"
    return f"{fmt_money(lo)} – {fmt_money(hi)}" if abs(hi - lo) > 0.005 else fmt_money(lo)


def fmt_pct_range(lo, hi):
    if lo is None or hi is None:
        return "—"
    return f"{fmt_pct(lo)} – {fmt_pct(hi)}" if abs(hi - lo) > 0.0005 else fmt_pct(lo)


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
st.session_state.setdefault("ticker", "META")
st.session_state.setdefault("reset_token", 0)
st.session_state.setdefault("years", 5)
st.session_state.setdefault("query", "")


def switch_ticker(new_ticker):
    new_ticker = new_ticker.strip().upper()
    if not new_ticker or new_ticker == st.session_state.ticker:
        return
    st.session_state.ticker = new_ticker
    st.session_state.reset_token += 1
    st.rerun()


def load_saved_row(row):
    st.session_state.ticker = str(row["ticker"]).strip().upper()
    st.session_state.reset_token += 1
    st.session_state.years = int(float(row["years"]))
    tok = st.session_state.reset_token
    st.session_state[f"eps_in_{tok}"] = float(row["starting_eps"])
    st.session_state[f"growth_in_{tok}"] = float(row["growth_rate"])
    pe_low_val = float(row["future_pe"])
    pe_high_val = row.get("pe_high") if hasattr(row, "get") else None
    pe_high_val = float(pe_high_val) if pe_high_val not in (None, "") and not pd.isna(pe_high_val) else pe_low_val
    st.session_state[f"pelow_in_{tok}"] = pe_low_val
    st.session_state[f"pehigh_in_{tok}"] = pe_high_val
    cur_price = _safe_float(row.get("current_price") if hasattr(row, "get") else row["current_price"])
    if cur_price is not None:
        st.session_state[f"curprice_in_{tok}"] = cur_price
    st.rerun()


# ---------------------------------------------------------------------------
# Sidebar — search + saved projections
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## 🔎 Search stock")
    col_q, col_go = st.columns([3, 1])
    with col_q:
        query = st.text_input(
            "Ticker or company", key="query",
            placeholder="e.g. META, NVIDIA, AAPL", label_visibility="collapsed",
        )
    with col_go:
        go_clicked = st.button("Go", width="stretch", key="go_btn")

    suggestions = search_tickers(query) if query and len(query) >= 2 else []
    options = [f"{t} — {n}" for t, n in suggestions]
    selected = st.selectbox(
        "Matches", options, index=None, placeholder="Select a match",
        key="match_select", label_visibility="collapsed",
    ) if options else None

    if go_clicked and query.strip():
        switch_ticker(query)
    elif selected:
        switch_ticker(selected.split(" — ", 1)[0])

    st.divider()
    st.markdown("## 💾 Saved Projections")
    saved_df, is_google = saved_all()
    if saved_df.empty:
        st.caption("No saved projections yet. Build one, then tap Save Projection.")
    else:
        for _, row in saved_df.head(20).iterrows():
            with st.container(border=True):
                _plo = _safe_float(row["future_pe"]) or 0.0
                _phi_raw = row.get("pe_high") if hasattr(row, "get") else None
                _phi = _safe_float(_phi_raw) if _phi_raw not in (None, "") else None
                _phi = _phi if _phi is not None and not pd.isna(_phi) else _plo
                _seps, _sgr, _syrs = _safe_float(row["starting_eps"]) or 0.0, _safe_float(row["growth_rate"]) or 0.0, int(float(row["years"]))
                _target_lo = _seps * ((1 + _sgr / 100) ** _syrs) * _plo
                _target_hi = _seps * ((1 + _sgr / 100) ** _syrs) * _phi
                st.markdown(
                    f"**{row['ticker']}** &nbsp; "
                    f"<span class='saved-sub'>{_syrs}Y · target {fmt_money_range(_target_lo, _target_hi)}</span>",
                    unsafe_allow_html=True,
                )
                b1, b2 = st.columns(2)
                with b1:
                    if st.button("Load", key=f"load_{row['saved_at']}", width="stretch"):
                        load_saved_row(row)
                with b2:
                    if st.button("Delete", key=f"delete_{row['saved_at']}", width="stretch"):
                        delete_projection(row["saved_at"], is_google)
                        st.rerun()

    st.divider()
    if is_google:
        st.caption("☁️ Persistence: Google Sheets — saves sync across devices and survive restarts.")
    else:
        st.caption("💾 Persistence: local SQLite file. Add Google Sheets credentials to secrets.toml to sync across devices.")

ticker = st.session_state.ticker

# ---------------------------------------------------------------------------
# Fetch data (graceful on failure — never hard-stops the app)
# ---------------------------------------------------------------------------
fetch_error = None
cache_age = None
try:
    quote = fetch_quote(ticker)
    _quote_cache_set(ticker, quote)
except Exception as e:
    cached = _quote_cache_get(ticker)
    if cached and cached["price"] is not None:
        quote = {"ticker": ticker, "company": cached["company"], "price": cached["price"],
                  "eps": cached["eps"], "pe": cached["pe"]}
        cache_age = cached["fetched_at"]
    else:
        quote = {"ticker": ticker, "company": ticker, "price": None, "eps": None, "pe": None}
    fetch_error = _friendly_fetch_error(e)

try:
    hist = fetch_historical_cagr(ticker)
except Exception:
    hist = {"rev": {1: None, 3: None, 4: None}, "eps": {1: None, 3: None, 4: None}}

live = fetch_error is None and quote["price"] is not None

# reset_token is needed now (not just further down) so the hero can reflect
# a manual Current Price override already sitting in session_state before
# that input widget itself has been drawn yet further down the page.
tok = st.session_state.reset_token
_price_override = st.session_state.get(f"curprice_in_{tok}")
if _price_override is not None:
    quote["price"] = _price_override

# ---------------------------------------------------------------------------
# Hero card
# ---------------------------------------------------------------------------
price_display = fmt_money(quote["price"]) if quote["price"] is not None else "—"
if live:
    status_class, status_text = "live", "● Live from Yahoo Finance"
elif cache_age and _price_override is None:
    status_class, status_text = "fallback", f"● {fetch_error} — showing cached data from {cache_age[11:16]}"
elif _price_override is not None:
    status_class, status_text = "fallback", "● Using your entered values"
else:
    status_class, status_text = "fallback", f"● {fetch_error or 'No data yet'} — enter values manually below"

with st.container(key="hero-card"):
    col_info, col_save = st.columns([4, 1])
    with col_info:
        st.markdown(
            f"""
            <div style="display:flex;align-items:center;gap:16px;padding:16px 18px;">
              <div style="flex:1;min-width:0;">
                <div style="display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;">
                  <span class="hero-name">{quote['company']}</span>
                  <span class="hero-ticker">{quote['ticker']}</span>
                </div>
                <div style="display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-top:4px;">
                  <span class="hero-price">{price_display}</span>
                  <span class="hero-status {status_class}">{status_text}</span>
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col_save:
        st.markdown("<div style='padding-top:22px'></div>", unsafe_allow_html=True)
        save_clicked = st.button("💾 Save", type="primary", width="stretch", key="save_btn")

# ---------------------------------------------------------------------------
# Horizon slider
# ---------------------------------------------------------------------------
years = st.slider("Projection horizon", 1, 10, key="years", format="%d years")

# ---------------------------------------------------------------------------
# Two-column cards
# ---------------------------------------------------------------------------
left, right = st.columns(2, gap="large")

with left:
    with st.container(border=True, key="metrics-card"):
        st.markdown("##### CURRENT METRICS")
        m1, m2 = st.columns(2)
        with m1:
            quote["eps"] = st.number_input(
                "EPS TTM", step=0.01, format="%.2f",
                key=f"epsttm_in_{tok}", **_default_kw(f"epsttm_in_{tok}", float(quote["eps"] or 0.0)),
            )
        with m2:
            quote["pe"] = st.number_input(
                "Current P/E", step=0.1, format="%.1f",
                key=f"pettm_in_{tok}", **_default_kw(f"pettm_in_{tok}", float(quote["pe"] or 0.0)),
            )
        quote["price"] = st.number_input(
            "Current Price ($)", min_value=0.0, step=0.01, format="%.2f",
            key=f"curprice_in_{tok}", **_default_kw(f"curprice_in_{tok}", float(quote["price"] or 0.0)),
        )
        if not live:
            note = (
                f"Showing the last successful fetch (from {cache_age[11:16]}) since live data "
                "didn't come through this time — all three fields above are editable if you'd rather "
                "enter fresher numbers yourself."
            ) if cache_age else (
                "Auto-fetch didn't come through this time — the three fields above are all editable, "
                "so you can fill them in yourself and the rest of the tool works exactly the same."
            )
            st.markdown(f"<span class='small-note'>{note}</span>", unsafe_allow_html=True)

        cagr_df = pd.DataFrame({
            "CAGR": ["Revenue", "EPS"],
            "1Y": [hist["rev"][1], hist["eps"][1]],
            "3Y": [hist["rev"][3], hist["eps"][3]],
            "4Y": [hist["rev"][4], hist["eps"][4]],
        })

        def _cagr_color(v):
            if v is None or pd.isna(v):
                return "color:#9aa1ac"
            return f"color:{GREEN if v >= 0 else RED}; font-weight:700; text-shadow:{'0 0 8px ' + GLOW if v >= 0 else 'none'}"

        def _cagr_fmt(v):
            return "—" if v is None or pd.isna(v) else f"{v * 100:+.1f}%"

        styled = cagr_df.style.map(_cagr_color, subset=["1Y", "3Y", "4Y"]).format(_cagr_fmt, subset=["1Y", "3Y", "4Y"])
        st.dataframe(styled, hide_index=True)  # width already defaults to 'stretch'

with right:
    with st.container(border=True, key="inputs-card"):
        st.markdown("##### PROJECTION INPUTS")
        starting_eps = st.number_input(
            "Starting EPS", step=0.01, format="%.2f",
            key=f"eps_in_{tok}", **_default_kw(f"eps_in_{tok}", float(quote["eps"] or 0.0)),
        )
        growth = st.number_input(
            "Growth Rate (%)", step=0.5, format="%.1f",
            key=f"growth_in_{tok}", **_default_kw(f"growth_in_{tok}", DEFAULT_GROWTH),
        )
        pe_default = float(quote["pe"] or DEFAULT_PE)
        pe_low, pe_high = st.columns(2)
        with pe_low:
            pe_low_val = st.number_input(
                "P/E Low", min_value=0.01, step=0.5, format="%.1f",
                key=f"pelow_in_{tok}", **_default_kw(f"pelow_in_{tok}", round(pe_default, 1)),
            )
        with pe_high:
            pe_high_val = st.number_input(
                "P/E High", min_value=0.01, step=0.5, format="%.1f",
                key=f"pehigh_in_{tok}", **_default_kw(f"pehigh_in_{tok}", round(pe_default * 1.3, 1)),
            )
        if pe_high_val < pe_low_val:
            pe_low_val, pe_high_val = pe_high_val, pe_low_val

# ---------------------------------------------------------------------------
# Compute projection — once per P/E scenario so the chart and results can
# show a range (bear case at P/E Low, bull case at P/E High) rather than a
# single line that implies false precision.
# ---------------------------------------------------------------------------
p_eps, p_price_low, annual_low, total_low = project(quote["price"], starting_eps, growth, pe_low_val, years)
_, p_price_high, annual_high, total_high = project(quote["price"], starting_eps, growth, pe_high_val, years)

# ---------------------------------------------------------------------------
# Reverse-CAGR target buy price — what you'd need to pay today for the P/E
# Low (conservative) case to still compound at your target rate. Anchored
# to the low case on purpose: a margin of safety means "even the bear case
# still hits my goal," not "the bull case might."
# ---------------------------------------------------------------------------
target_buy_price = (p_price_low / ((1 + TARGET_CAGR) ** years)) if p_price_low is not None else None
margin_pct = None
if target_buy_price is not None and quote["price"] and quote["price"] > 0:
    margin_pct = (target_buy_price - quote["price"]) / quote["price"] * 100

# ---------------------------------------------------------------------------
# Results — a 2x2 grid rather than 4-across so each card stays readable on a
# phone-width screen without depending on how narrow a single column gets.
# ---------------------------------------------------------------------------
r1, r2 = st.columns(2)
r3, r4 = st.columns(2)
with r1:
    with st.container(border=True, key="result-eps"):
        st.markdown(
            f'<span class="result-label">Projected EPS</span>'
            f'<span class="result-value">{fmt_money(p_eps)}</span>',
            unsafe_allow_html=True,
        )
with r2:
    with st.container(border=True, key="result-price"):
        cls = "positive" if (p_price_low or 0) >= (quote["price"] or 0) else "negative"
        st.markdown(
            f'<span class="result-label">Projected Price</span>'
            f'<span class="result-value {cls}">{fmt_money_range(p_price_low, p_price_high)}</span>',
            unsafe_allow_html=True,
        )
with r3:
    with st.container(border=True, key="result-annual"):
        cls = "positive" if (annual_low or 0) >= 0 else "negative"
        st.markdown(
            f'<span class="result-label">Annual Return</span>'
            f'<span class="result-value {cls}">{fmt_pct_range(annual_low, annual_high)}</span>',
            unsafe_allow_html=True,
        )
with r4:
    total_key = "result-total-pos" if (total_low or 0) >= 0 else "result-total-neg"
    with st.container(border=True, key=total_key):
        st.markdown(
            f'<span class="result-label">Total Return</span>'
            f'<span class="result-value">{fmt_pct_range(total_low, total_high)}</span>',
            unsafe_allow_html=True,
        )

# ---------------------------------------------------------------------------
# Chart — two trajectories (P/E Low and P/E High) so the line shows a range
# rather than one falsely-precise path, with a shaded band between them.
# ---------------------------------------------------------------------------
labels = build_labels(years)
prices_low = build_trajectory(quote["price"], annual_low, years)
prices_high = build_trajectory(quote["price"], annual_high, years)


def _rounded_rect_path(x0, y0, x1, y1, rx, ry):
    """SVG path for a rounded rectangle. rx/ry are corner radii in the same
    units as x/y respectively — kept separate because the badges mix a data
    x-axis with a paper-fraction y-axis, so a single shared radius would
    stretch into an ellipse rather than a even a corner."""
    return (
        f"M {x0+rx},{y0} L {x1-rx},{y0} Q {x1},{y0} {x1},{y0+ry} "
        f"L {x1},{y1-ry} Q {x1},{y1} {x1-rx},{y1} "
        f"L {x0+rx},{y1} Q {x0},{y1} {x0},{y1-ry} "
        f"L {x0},{y0+ry} Q {x0},{y0} {x0+rx},{y0} Z"
    )


with st.container(border=True, key="chart-card"):
    st.markdown(f"##### Projected Stock Price — {quote['ticker']}")
    if not quote["price"] or quote["price"] <= 0:
        st.info("Enter a Current Price in Current Metrics to see the projection chart.")
    else:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=labels, y=prices_low, mode="lines+markers", name=f"P/E {pe_low_val:.1f} (low)",
            line=dict(color="#94A3B8", width=3, shape="spline", smoothing=0.4),
            marker=dict(size=7, color="#ffffff", line=dict(color="#94A3B8", width=2)),
            hovertemplate="%{x}<br>P/E low: <b>$%{y:,.2f}</b><extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=labels, y=prices_high, mode="lines+markers", name=f"P/E {pe_high_val:.1f} (high)",
            line=dict(color=GREEN_BRIGHT, width=4, shape="spline", smoothing=0.4),
            marker=dict(size=8, color="#ffffff", line=dict(color=GREEN_BRIGHT, width=2.5)),
            fill="tonexty", fillcolor="rgba(22,163,74,0.10)",
            hovertemplate="%{x}<br>P/E high: <b>$%{y:,.2f}</b><extra></extra>",
        ))

        all_prices = prices_low + prices_high
        y_max = max(all_prices) if all_prices else 1
        y_min = min(all_prices) if all_prices else 0
        span_est = (y_max - y_min) or (y_max * 0.3 if y_max else 100)
        badge_offset = 0.11 * span_est   # vertical gap between a point and its badge, in price $
        badge_half_h = 0.030 * span_est  # badge height, in price $ — scales with the chart's own range

        def _badge(x_idx, y_price, text, above=True):
            half_w = 0.11 + 0.037 * len(text)  # data-x units — scales with digit count, not fixed
            cy = y_price + (badge_offset if above else -badge_offset)
            fig.add_shape(
                type="path",
                path=_rounded_rect_path(x_idx - half_w, cy - badge_half_h, x_idx + half_w, cy + badge_half_h,
                                         0.05, badge_half_h * 0.4),
                xref="x", yref="y", fillcolor=INK, line=dict(width=0),
            )
            fig.add_annotation(
                x=x_idx, y=cy, xref="x", yref="y", text=text, showarrow=False,
                xanchor="center", yanchor="middle",
                font=dict(color="#ffffff", size=12, family="Space Grotesk", weight="bold"),
            )

        # Year 0 is identical on both lines (today's actual price) — one shared
        # badge. After that, low/high diverge, so each gets its own badge on
        # opposite sides of the line to keep the two from colliding.
        _badge(0, prices_high[0], f"${prices_high[0]:,.0f}", above=True)
        for i in range(1, years + 1):
            _badge(i, prices_high[i], f"${prices_high[i]:,.0f}", above=True)
            _badge(i, prices_low[i], f"${prices_low[i]:,.0f}", above=False)

        # Range follows the badges' own real extents (not a separate estimate),
        # so nothing can end up outside it — including in a steep-decline
        # scenario where a badge would otherwise sit below a $0-clamped floor.
        top_extent = y_max + badge_offset + badge_half_h
        bottom_extent = y_min - badge_offset - badge_half_h
        y_range = [bottom_extent - abs(bottom_extent) * 0.04, top_extent * 1.04]

        fig.update_layout(
            height=460,
            margin=dict(l=20, r=70, t=45, b=35),
            plot_bgcolor="white", paper_bgcolor="white",
            xaxis=dict(
                showgrid=True, gridcolor="#eef0f4", title="",
                tickfont=dict(color=INK, size=13, family="Inter", weight="bold"),
            ),
            yaxis=dict(
                side="right", showgrid=True, gridcolor="#eef0f4",
                tickprefix="$", tickformat=",.0f", color=GREEN_BRIGHT,
                range=y_range,
            ),
            font=dict(color=INK, family="Inter"),
            hovermode="x unified",
            showlegend=True,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig)  # width already defaults to 'stretch'

    if target_buy_price is not None:
        years_word = "year" if years == 1 else "years"
        if margin_pct is not None:
            if margin_pct >= 0:
                tint = "positive"
                tail = f"that's a {margin_pct:.1f}% margin of safety versus today's {fmt_money(quote['price'])}"
            else:
                tint = "caution"
                tail = f"the price would need to fall {abs(margin_pct):.1f}% from today's {fmt_money(quote['price'])} to get there"
        else:
            tint = "neutral"
            tail = "enter a current price above to compare it against today's price"
        st.markdown(
            f"<div class='margin-strip {tint}'>To hit your {TARGET_CAGR*100:.0f}% CAGR goal over {years} "
            f"{years_word} (using the P/E Low case), your target buy price is "
            f"<strong>{fmt_money(target_buy_price)}</strong> — {tail}.</div>",
            unsafe_allow_html=True,
        )

# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------
if save_clicked:
    row = (
        datetime.now().isoformat(timespec="seconds"), quote["ticker"], quote["company"],
        quote["price"] or 0.0, years, starting_eps, growth, pe_low_val,
        p_eps or 0.0, p_price_high or 0.0, annual_high or 0.0, total_high or 0.0,
        pe_high_val,
    )
    save_projection(row)
    st.toast(f"Saved {quote['ticker']} projection", icon="✅")
    st.rerun()

st.divider()
st.caption("Data source: Yahoo Finance via yfinance. Educational tool for scenario analysis — not investment advice.")
st.caption("Luke Bruni Invests")
