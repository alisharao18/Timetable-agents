"""Plain, professional styling: navy blue, red accents, black text on white."""
import streamlit as st

NAVY, RED, BLACK, WHITE = "#0B2A5B", "#C8102E", "#111111", "#FFFFFF"
MIST, LINE, SLATE = "#F3F6FB", "#D5DCE8", "#44506A"

_CSS = f"""
<style>
:root {{ --navy:{NAVY}; --red:{RED}; --black:{BLACK}; --mist:{MIST}; --line:{LINE}; --slate:{SLATE}; }}
html, body, [class*="css"] {{ font-family: "Segoe UI", system-ui, -apple-system, Roboto, Arial, sans-serif; }}
.stApp {{ background:{WHITE}; color:{BLACK}; }}
.block-container {{ padding-top: 1.2rem; max-width: 1280px; }}
h1, h2, h3 {{ color:{NAVY}; letter-spacing:-0.01em; }}
h2 {{ border-bottom: 2px solid {LINE}; padding-bottom: .3rem; }}
section[data-testid="stSidebar"] {{ background:{NAVY}; }}
section[data-testid="stSidebar"] * {{ color:{WHITE}; }}
section[data-testid="stSidebar"] hr {{ border-color: rgba(255,255,255,.25); }}
section[data-testid="stSidebar"] .stButton > button {{
  background: transparent; color:{WHITE}; border:1px solid rgba(255,255,255,.55); }}
section[data-testid="stSidebar"] .stButton > button:hover {{ background: rgba(255,255,255,.12); border-color:{WHITE}; }}
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {{
  border-radius: 4px; border: 1px solid {NAVY}; color:{NAVY}; background:{WHITE}; font-weight: 600; }}
.stButton > button:hover, .stDownloadButton > button:hover, .stFormSubmitButton > button:hover {{
  background:{MIST}; color:{NAVY}; border-color:{NAVY}; }}
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {{
  background:{NAVY}; color:{WHITE}; border-color:{NAVY}; }}
.stButton > button[kind="primary"]:hover, .stFormSubmitButton > button[kind="primary"]:hover {{
  background:#081F45; color:{WHITE}; }}
div[data-testid="stMetric"] {{ background:{MIST}; border:1px solid {LINE}; border-left:4px solid {NAVY};
  border-radius:4px; padding:.6rem .8rem; }}
div[data-testid="stMetricLabel"] {{ color:{SLATE}; }}
div[data-testid="stMetricValue"] {{ color:{BLACK}; }}
.tt-header {{ background:{NAVY}; color:{WHITE}; padding:1rem 1.25rem; border-radius:4px;
  border-bottom:4px solid {RED}; margin-bottom:1rem; }}
.tt-header h1 {{ color:{WHITE}; margin:0; font-size:1.55rem; padding:0; }}
.tt-header p {{ color:#D7E0F0; margin:.2rem 0 0; font-size:.95rem; }}
.tt-pick {{ border:1px solid {LINE}; border-left:5px solid {NAVY}; border-radius:4px;
  padding:.8rem 1rem; background:{MIST}; margin:.4rem 0 .8rem; }}
.tt-pick b {{ color:{NAVY}; font-size:1.25rem; }}
.tt-pick span {{ color:{SLATE}; }}
.tt-tag {{ display:inline-block; padding:.05rem .5rem; border-radius:3px; font-size:.78rem;
  font-weight:600; margin-left:.4rem; background:{NAVY}; color:{WHITE}; }}
.tt-tag.red {{ background:{RED}; }}
.tt-grid {{ width:100%; border-collapse:collapse; table-layout:fixed; font-size:.82rem; }}
.tt-grid th {{ background:{NAVY}; color:{WHITE}; padding:.4rem; border:1px solid {NAVY}; }}
.tt-grid th.hr {{ background:{BLACK}; width:84px; }}
.tt-grid td {{ border:1px solid {LINE}; padding:.35rem .4rem; vertical-align:top; height:3.1rem;
  color:{BLACK}; white-space:pre-line; overflow-wrap:anywhere; }}
.tt-grid td.on {{ background:{MIST}; border-left:3px solid {NAVY}; }}
.tt-grid td.booked {{ background:#FDECEF; border-left:3px solid {RED}; }}
.tt-grid td.lunch {{ background:#FAFAFA; }}
</style>
"""


def inject_theme():
    st.markdown(_CSS, unsafe_allow_html=True)


def header(title, subtitle=""):
    st.markdown(f'<div class="tt-header"><h1>{title}</h1><p>{subtitle}</p></div>',
                unsafe_allow_html=True)


def grid_html(table):
    """A timetable DataFrame (hours x days of text) as a readable HTML grid."""
    from html import escape
    out = ['<table class="tt-grid"><tr><th class="hr">Time</th>']
    out += [f"<th>{escape(str(c))}</th>" for c in table.columns]
    out.append("</tr>")
    for idx, row in table.iterrows():
        out.append(f'<tr><th class="hr">{escape(str(idx))}</th>')
        for v in row:
            cls = "" if not v else ("booked" if "BOOKED" in v or v.startswith("CHECK") else "on")
            out.append(f'<td class="{cls}">{escape(str(v))}</td>')
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)
