#!/usr/bin/env python3
"""GAP Assortment Radar — shared Nordic gap workspace (repo-backed read/write).

Three departments work here: Merchant Acquisition, Category Success, Merchant Success.
Two branches, handled separately: CDON and Fyndiq (own findings + own status files).

Data files in the repo johanna-stack/gap-assortment-radar:
  findings.json         CDON gaps    (pipeline writes, additive merge — never overwrites)
  findings-fyndiq.json  Fyndiq gaps  (same model, separate pipeline)
  state.json            CDON status/owner/comments   (THIS APP writes, pipeline never touches)
  state-fyndiq.json     Fyndiq status/owner/comments

Status is per BRAND (one conversation with a merchant covers all markets); the
market breadth (SE/NO/DK/FI gap or in assortment) is shown as chips on the same row.

Local:  export GAP_RADAR_PAT=...  &&  streamlit run streamlit_app.py
Cloud:  set secret GAP_RADAR_PAT
Without token: read-only against the bundled findings.json.
"""
import os
import re
import json
import base64
import datetime

import requests
import pandas as pd
import streamlit as st

REPO = "johanna-stack/gap-assortment-radar"
API = f"https://api.github.com/repos/{REPO}/contents"
ACQ = "Merchant Acquisition"

BRANCHES = {
    "CDON": {"findings": "findings.json", "state": "state.json",
             "accent": "#5b8db8", "tagline": "Seasonal + merchant calendar driven"},
    "Fyndiq": {"findings": "findings-fyndiq.json", "state": "state-fyndiq.json",
               "accent": "#a08bba", "tagline": "Own seasonal calendar - fast on viral trends"},
}

STATUSES = ["New", "Contacted", "On hold", "Live"]
STATUS_KEY = {"New": "ny", "Contacted": "kontaktad", "On hold": "avvaktar", "Live": "live"}
KEY_STATUS = {v: k for k, v in STATUS_KEY.items()}
# legacy per-market keys from the old app -> brand-level status, strongest wins
LEGACY_RANK = {"gap": 0, "kontaktad": 1, "avvakta": 2, "live": 3}
LEGACY_TO_NEW = {"gap": "ny", "kontaktad": "kontaktad", "avvakta": "avvaktar", "live": "live"}

DEPARTMENTS = ["Merchant Acquisition", "Merchant Success"]
# Muted palette - easy on the eyes, still readable in both light and dark theme.
STATUS_COLOR = {"New": "#b97a7f", "Contacted": "#c9a06a", "On hold": "#8d99ae", "Live": "#7f9c87"}

TYP_CANON = {
    "Kategori-uppstickare": "Category up-and-comer",
    "Peak-modell": "Peak model",
    "Category up-and-comer": "Category up-and-comer",
    "Peak model": "Peak model",
}


def canon_typ(t):
    return TYP_CANON.get((t or "").strip(), (t or "").strip() or "Category up-and-comer")


st.set_page_config(page_title="GAP Assortment Radar", layout="wide", page_icon=None)

st.markdown("""
<style>
.block-container {padding-top: 1.6rem;}
.kpi-card {border: 1px solid rgba(128,128,128,.25); border-radius: 10px;
           padding: .65rem .9rem; text-align: center;}
.kpi-card .v {font-size: 1.55rem; font-weight: 700; line-height: 1.2;}
.kpi-card .l {font-size: .72rem; opacity: .65; text-transform: uppercase; letter-spacing: .05em;}
.mkt {display: inline-block; min-width: 2.4em; text-align: center; padding: .1em .35em;
      border-radius: 6px; font-size: .74rem; font-weight: 700; margin-right: .25em;}
.mkt-gapdemand {background: #b97a7f; color: #fff;}
.mkt-gap {background: #d9c08f; color: #3a3325;}
.mkt-in {background: #7f9c87; color: #fff;}
.mkt-na {background: rgba(128,128,128,.15); color: #888;}
.badge {display: inline-block; padding: .12em .55em; border-radius: 999px;
        font-size: .72rem; font-weight: 700; color: #fff;}
.brand-line {font-size: 1.02rem; font-weight: 700;}
.dim {opacity: .6; font-size: .8rem;}
.signal {color: #c07a50; font-weight: 600; font-size: .8rem;}
.legend {font-size: .78rem; opacity: .85; margin: .2rem 0 .6rem 0;}
</style>
""", unsafe_allow_html=True)


def _token():
    try:
        if "GAP_RADAR_PAT" in st.secrets:
            return str(st.secrets["GAP_RADAR_PAT"])
    except Exception:
        pass
    return os.environ.get("GAP_RADAR_PAT")


TOKEN = _token()
READONLY = TOKEN is None


def _headers():
    h = {"Accept": "application/vnd.github+json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


@st.cache_data(ttl=15)
def gh_get(path):
    r = requests.get(f"{API}/{path}?ref=main", headers=_headers(), timeout=20)
    if r.status_code == 404:
        return None, None
    r.raise_for_status()
    j = r.json()
    return json.loads(base64.b64decode(j["content"]).decode("utf-8")), j["sha"]


def gh_put(path, obj, sha, message):
    body = {"message": message,
            "content": base64.b64encode(json.dumps(obj, ensure_ascii=False, indent=2).encode()).decode(),
            "branch": "main"}
    if sha:
        body["sha"] = sha
    r = requests.put(f"{API}/{path}", headers=_headers(), json=body, timeout=20)
    r.raise_for_status()
    return r.json()["content"]["sha"]


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "x").lower()).strip("-")[:40]


def signal_rank(s):
    s = (s or "").strip().lower()
    if not s:
        return -1.0
    if "breakout" in s:
        return float("inf")
    m = re.search(r"(\d[\d\s.,]*)", s)
    if m:
        try:
            return float(m.group(1).replace(" ", "").replace(",", "").rstrip("."))
        except ValueError:
            return 0.0
    return 0.0


# ---------------- branch switch ----------------
hdr_l, hdr_r = st.columns([3, 2])
with hdr_l:
    st.title("GAP Assortment Radar")
with hdr_r:
    branch = st.radio("Branch", list(BRANCHES), horizontal=True, label_visibility="collapsed")
B = BRANCHES[branch]
st.markdown(
    f"<span class='badge' style='background:{B['accent']}'>{branch}</span> "
    f"<span class='dim'>{B['tagline']}</span>", unsafe_allow_html=True)

# ---------------- data ----------------
findings_doc = None
if TOKEN:
    try:
        findings_doc, _ = gh_get(B["findings"])
    except Exception as e:  # noqa: BLE001
        st.warning(f"Could not read {B['findings']} from repo: {e}")
if findings_doc is None and branch == "CDON":
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "findings.json"),
                  encoding="utf-8") as fh:
            findings_doc = json.load(fh)
    except Exception:  # noqa: BLE001
        findings_doc = None
if findings_doc is None:
    findings_doc = {"markets": ["SE", "NO", "DK", "FI"], "category": "", "findings": []}

markets = findings_doc.get("markets") or ["SE", "NO", "DK", "FI"]

state_key = f"state::{branch}"
if state_key not in st.session_state:
    s, sha = ({}, None)
    if TOKEN:
        try:
            s, sha = gh_get(B["state"])
            s = s or {}
        except Exception:  # noqa: BLE001
            s, sha = {}, None
    st.session_state[state_key] = s
    st.session_state[state_key + "::sha"] = sha
STATE = st.session_state[state_key]


def entry(bid):
    """State record for a brand, migrating the old per-market model on first touch.
    Old: {markets: {SE: 'kontaktad', ...}, comment: 'text'}
    New: {status: 'kontaktad', owner: '', comments: [{user, ts, text}]}"""
    e = STATE.setdefault(bid, {})
    if "status" not in e:
        legacy = e.get("markets") or {}
        best = max(legacy.values(), key=lambda v: LEGACY_RANK.get(v, 0), default="gap")
        e["status"] = LEGACY_TO_NEW.get(best, "ny")
    if "owner" not in e:
        e["owner"] = ""
    if "comments" not in e:
        old = (e.get("comment") or "").strip()
        e["comments"] = ([{"user": "earlier note", "ts": "", "text": old}] if old else [])
    return e


def save_state(message):
    try:
        st.session_state[state_key + "::sha"] = gh_put(
            B["state"], STATE, st.session_state[state_key + "::sha"], message)
        gh_get.clear()
        st.toast("Saved to shared source (repo)")
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else None
        if code in (409, 422):
            st.error("Save conflict - someone saved at the same time. Reloading, please try again.")
            del st.session_state[state_key]
            st.rerun()
        else:
            st.error(f"Could not save: {e}")


# pivot findings -> one record per (category, brand)
brands = {}
for f in findings_doc.get("findings", []):
    catn = f.get("category") or findings_doc.get("category") or "-"
    key = f"{catn}␟{f['brand']}"
    b = brands.setdefault(key, {"id": key, "brand": f["brand"], "category": catn,
                                "base": {}, "note": "", "signal": "", "merchants": [],
                                "demand": set(), "typ": "Category up-and-comer",
                                "first_seen": f.get("first_seen", "")})
    b["base"][f["market"]] = "in" if f.get("in_cdon") else "gap"
    if f.get("demand"):
        b["demand"].add(f["market"])
    if f.get("typ"):
        b["typ"] = canon_typ(f["typ"])
    if f.get("signal") and not b["signal"]:
        b["signal"] = f["signal"]
    if not b["note"] and f.get("note"):
        b["note"] = f["note"]
    if f.get("first_seen", "") > b["first_seen"]:
        b["first_seen"] = f.get("first_seen", "")
    for m in f.get("merchants_selling", []):
        b["merchants"].append({**m, "market": f["market"]})


def brand_status(b):
    return KEY_STATUS.get(entry(b["id"]).get("status", "ny"), "New")


DEPT_COLOR = {"Merchant Acquisition": "#b08c9d", "Merchant Success": "#9dab8c"}


def derived_dept(b):
    """Automatisk routing - inget manuellt val:
    - en befintlig merchant säljer brandet på egen sajt, ELLER det säljs redan
      hos oss men saknas på någon marknad -> Merchant Success (aktivera/bredda)
    - ingen har det -> Merchant Acquisition (rekrytera)"""
    if any(v == "in" for v in b["base"].values()) or b["merchants"]:
        return "Merchant Success"
    return "Merchant Acquisition"


def market_chips(b):
    parts = []
    for m in markets:
        base = b["base"].get(m)
        if base == "in":
            cls, tip = "mkt-in", "in assortment"
        elif base == "gap" and m in b["demand"]:
            cls, tip = "mkt-gapdemand", "GAP + rising demand"
        elif base == "gap":
            cls, tip = "mkt-gap", "GAP (no trend signal yet)"
        else:
            cls, tip = "mkt-na", "not checked"
        parts.append(f"<span class='mkt {cls}' title='{m}: {tip}'>{m}</span>")
    return "".join(parts)


def route_in(b):
    sellers = sorted({x["merchant"] for x in b["merchants"]})
    return ", ".join(sellers) if sellers else ACQ


mode = "read/write" if not READONLY else "read-only"
st.caption(f"Shared source: repo {REPO} ({mode}) - {len(brands)} brands - markets {', '.join(markets)}"
           + (f" - last run {findings_doc.get('updated')}" if findings_doc.get("updated") else ""))
if READONLY:
    st.warning("Read-only: no GAP_RADAR_PAT secret set, status and comments cannot be saved.")

# ---------------- sidebar ----------------
with st.sidebar:
    st.header("You")
    user = st.text_input("Your name (for comments)", st.session_state.get("user", ""),
                         placeholder="e.g. Johanna")
    st.session_state["user"] = user
    st.header("Filter")
    f_dept = st.selectbox("Department", ["All"] + DEPARTMENTS,
                          help="Auto-routed: an existing merchant has it (own site or "
                               "already on our platform in some market) = Merchant Success, "
                               "nobody has it = Merchant Acquisition")
    f_status = st.multiselect("Status", STATUSES, default=[])
    cats = sorted({b["category"] for b in brands.values()})
    f_cat = st.selectbox("Category", ["All"] + cats)
    f_typ = st.selectbox("Type", ["All", "Category up-and-comer", "Peak model"])
    f_q = st.text_input("Search brand / merchant")
    if st.button("Refresh data", use_container_width=True,
                 help="Fetch the latest findings + status from the repo"):
        gh_get.clear()
        st.session_state.pop(state_key, None)
        st.rerun()


def visible(b):
    if f_cat != "All" and b["category"] != f_cat:
        return False
    if f_typ != "All" and b["typ"] != f_typ:
        return False
    if f_status and brand_status(b) not in f_status:
        return False
    if f_dept != "All" and derived_dept(b) != f_dept:
        return False
    if f_q:
        hay = b["brand"].lower() + " " + " ".join(x["merchant"].lower() for x in b["merchants"])
        if f_q.lower() not in hay:
            return False
    return True


vis_brands = [b for b in brands.values() if visible(b)]

# ---------------- KPI row ----------------
last_run = findings_doc.get("updated", "")
counts = {s: 0 for s in STATUSES}
new_this_run = 0
for b in brands.values():
    counts[brand_status(b)] += 1
    if b["first_seen"] and b["first_seen"] == last_run:
        new_this_run += 1

k = st.columns(6)
kpis = [("Brands", len(brands)), ("New in last run", new_this_run),
        ("New (untouched)", counts["New"]), ("Contacted", counts["Contacted"]),
        ("On hold", counts["On hold"]), ("Live", counts["Live"])]
for col, (label, val) in zip(k, kpis):
    color = STATUS_COLOR.get(label.replace(" (untouched)", ""), B["accent"])
    col.markdown(f"<div class='kpi-card'><div class='v' style='color:{color}'>{val}</div>"
                 f"<div class='l'>{label}</div></div>", unsafe_allow_html=True)

st.write("")

if not brands:
    if branch == "Fyndiq":
        st.info("No Fyndiq findings yet. The Fyndiq pipeline is activated (own seasonal "
                "calendar + Steep gap-check against the fyndiq branch) and runs every Monday - "
                "this workspace populates automatically after the first run.")
    else:
        st.info("No findings for this branch yet.")
    st.stop()

tab_work, tab_merchant, tab_viral, tab_matrix = st.tabs(
    ["Workspace", "By merchant", "Viral radar", "Market matrix"])


def render_brand_row(b, key_prefix):
    e = entry(b["id"])
    c1, c2, c3, c4, c5 = st.columns([3.2, 2.0, 1.6, 1.7, 1.3])
    with c1:
        st.markdown(f"<div class='brand-line'>{b['brand']}</div>"
                    f"<div class='dim'>{b['category']} - {b['typ']}</div>",
                    unsafe_allow_html=True)
        if b["signal"]:
            st.markdown(f"<span class='signal'>{b['signal']}</span>", unsafe_allow_html=True)
    with c2:
        st.markdown(market_chips(b), unsafe_allow_html=True)
        st.markdown(f"<div class='dim'>Route in: {route_in(b)}</div>", unsafe_allow_html=True)
    with c3:
        cur = brand_status(b)
        new = st.selectbox("Status", STATUSES, index=STATUSES.index(cur),
                           key=f"{key_prefix}st_{slug(b['id'])}", disabled=READONLY,
                           label_visibility="collapsed")
        if not READONLY and new != cur:
            e["status"] = STATUS_KEY[new]
            save_state(f"{branch}: {b['brand']} -> {new}")
            st.rerun()
    with c4:
        dept = derived_dept(b)
        st.markdown(f"<span class='badge' style='background:{DEPT_COLOR[dept]}'>{dept}</span>",
                    unsafe_allow_html=True)
    with c5:
        n = len(e["comments"])
        with st.popover(f"Comments ({n})", use_container_width=True):
            for c in e["comments"]:
                who = c.get("user") or "?"
                ts = (c.get("ts") or "")[:10]
                st.markdown(f"**{who}** <span class='dim'>{ts}</span><br>{c.get('text', '')}",
                            unsafe_allow_html=True)
                st.divider()
            if not READONLY:
                txt = st.text_area("New comment", key=f"{key_prefix}cm_{slug(b['id'])}",
                                   placeholder="What was said / agreed / next step")
                if st.button("Add comment", key=f"{key_prefix}cb_{slug(b['id'])}"):
                    if txt.strip():
                        e["comments"].append({
                            "user": st.session_state.get("user") or "anonymous",
                            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
                            "text": txt.strip()})
                        save_state(f"{branch}: comment on {b['brand']}")
                        st.rerun()
    st.divider()


LEGEND_HTML = (
    "<div class='legend'>"
    "<span class='mkt mkt-gapdemand'>SE</span> gap + rising demand (act now) &nbsp; "
    "<span class='mkt mkt-gap'>SE</span> gap, no trend signal yet (breadth potential) &nbsp; "
    "<span class='mkt mkt-in'>SE</span> already in assortment &nbsp; "
    "<span class='mkt mkt-na'>SE</span> not checked"
    "</div>")

with tab_work:
    st.caption("One row per brand - status applies to the whole brand since one merchant "
               "conversation covers all markets. Department routing is automatic: "
               "an existing merchant has it (own site, or already on our platform but "
               "missing in a market) = Merchant Success - nobody has it = Merchant Acquisition.")
    st.markdown(LEGEND_HTML, unsafe_allow_html=True)
    groups = {s: [] for s in STATUSES}
    for b in vis_brands:
        groups[brand_status(b)].append(b)
    for s in STATUSES:
        if not groups[s]:
            continue
        color = STATUS_COLOR[s]
        st.markdown(f"<span class='badge' style='background:{color}'>{s} - {len(groups[s])}</span>",
                    unsafe_allow_html=True)
        for b in sorted(groups[s], key=lambda x: (-signal_rank(x["signal"]), x["brand"])):
            render_brand_row(b, f"w{slug(s)}_")

with tab_merchant:
    st.caption("Gaps grouped by the merchant who already sells the brand outside our platform - "
               "the warm route in. One click marks the whole conversation as contacted.")
    mgroups = {}
    for b in vis_brands:
        sellers = sorted({x["merchant"] for x in b["merchants"]})
        if sellers:
            for s in sellers:
                mgroups.setdefault(s, []).append(b)
        else:
            mgroups.setdefault(ACQ, []).append(b)
    names = sorted(mgroups, key=lambda n: (n == ACQ, n.lower()))
    for name in names:
        bs = mgroups[name]
        sites = sorted({x.get("site", "") for b in bs for x in b["merchants"]
                        if x["merchant"] == name and x.get("site")})
        with st.expander(f"{name}  ({len(bs)} brands)", expanded=bool(f_q)):
            if sites:
                st.caption(" - ".join(sites))
            if not READONLY and name != ACQ and st.button(
                    f"Mark all as Contacted", key=f"all_{slug(name)}"):
                for b in bs:
                    if entry(b["id"])["status"] == "ny":
                        entry(b["id"])["status"] = "kontaktad"
                save_state(f"{branch}: contacted all via {name}")
                st.rerun()
            for b in bs:
                render_brand_row(b, f"m{slug(name)}_")

with tab_viral:
    st.caption("Hottest signals first - Breakout is Google Trends' strongest label. "
               + ("Fyndiq's edge is speed: activate before the trend peaks."
                  if branch == "Fyndiq" else
                  "For CDON these feed seasonal assortment; for fast activation check the Fyndiq branch."))
    rows = []
    for b in vis_brands:
        if signal_rank(b["signal"]) <= 0:
            continue
        rows.append({"Signal": b["signal"], "Brand": b["brand"], "Category": b["category"],
                     "Type": b["typ"],
                     "Demand in": ", ".join(sorted(b["demand"])) or "-",
                     "Gap in": ", ".join(m for m in markets if b["base"].get(m) == "gap") or "-",
                     "Route in": route_in(b), "Status": brand_status(b),
                     "_rank": signal_rank(b["signal"])})
    if rows:
        df = (pd.DataFrame(rows).sort_values("_rank", ascending=False)
              .drop(columns=["_rank"]).reset_index(drop=True))
        st.dataframe(df, hide_index=True, use_container_width=True)
        st.download_button("Export CSV", df.to_csv(index=False).encode("utf-8"),
                           file_name=f"viral_radar_{branch.lower()}_{datetime.date.today()}.csv",
                           mime="text/csv")
    else:
        st.info("No trend signals for the selected filter.")

with tab_matrix:
    st.caption("Market breadth per brand. in = in assortment - GAP+trend = missing with rising "
               "demand (act) - GAP = missing, no trend yet (breadth potential).")
    rows = []
    for b in vis_brands:
        r = {"Brand": b["brand"], "Category": b["category"], "Status": brand_status(b),
             "Department": derived_dept(b)}
        for m in markets:
            base = b["base"].get(m)
            r[m] = ("in" if base == "in"
                    else ("GAP+trend" if base == "gap" and m in b["demand"]
                          else ("GAP" if base == "gap" else "-")))
        r["Gaps"] = sum(1 for m in markets if r[m].startswith("GAP"))
        rows.append(r)
    if rows:
        df = pd.DataFrame(rows).sort_values(["Gaps", "Brand"],
                                            ascending=[False, True]).reset_index(drop=True)
        st.dataframe(df, hide_index=True, use_container_width=True)
        st.download_button("Export CSV", df.to_csv(index=False).encode("utf-8"),
                           file_name=f"market_matrix_{branch.lower()}_{datetime.date.today()}.csv",
                           mime="text/csv")
    else:
        st.info("No brands for the selected filter.")
