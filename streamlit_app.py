#!/usr/bin/env python3
"""GAP Assortment Radar - Streamlit app (repo-backed read/write).

Reads findings.json (gaps, pushed by Aurora) + state.json (status) from the GitHub repo
johanna-stack/gap-assortment-radar, and writes status back to state.json via the
GitHub Contents API using GAP_RADAR_PAT. No backend/Mac required.

IMPORTANT: GAP_RADAR_PAT is scoped to ONLY this repo. cdon-trackers is never touched.

Local:  export GAP_RADAR_PAT=...   &&  streamlit run streamlit_app.py
Cloud:  set secret  GAP_RADAR_PAT = "github_pat_..."
Without token: read-only against the bundled findings.json (status cannot be saved).
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
# Internal stored status keys (in state.json) stay unchanged for compatibility;
# only the displayed labels are English.
LABEL = {"gap": "GAP", "kontaktad": "Contacted", "avvakta": "On hold", "live": "Live"}
INV = {v: k for k, v in LABEL.items()}
GROUP_OF = {"GAP": "New", "Contacted": "Contacted", "On hold": "On hold", "Live": "Done"}
STATUS_GROUPS = ["New", "Contacted", "On hold", "Done"]
# Canonicalize the finding type to English (data may still carry the old Swedish values).
TYP_CANON = {
    "Kategori-uppstickare": "Category up-and-comer",
    "Peak-modell": "Peak model",
    "Category up-and-comer": "Category up-and-comer",
    "Peak model": "Peak model",
}
TYP_ORDER = ["Category up-and-comer", "Peak model"]


def canon_typ(t):
    return TYP_CANON.get((t or "").strip(), (t or "").strip() or "Category up-and-comer")


st.set_page_config(page_title="GAP Assortment Radar", layout="wide")


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


def _norm(x):
    """Normalize a cell value for comparison: NaN/None -> "". Otherwise empty
    Comment cells (NaN in data_editor) would always differ from "" and trigger an
    infinite save->rerun loop."""
    if x is None:
        return ""
    try:
        if pd.isna(x):
            return ""
    except (TypeError, ValueError):
        pass
    return str(x).strip()


def signal_rank(s):
    """Numeric strength of a trend signal, for ranking (highest = hottest).
    Breakout is the strongest Google Trends label, then the rising percentage."""
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


# --- findings (gaps) ---
findings_doc = None
if TOKEN:
    try:
        findings_doc, _ = gh_get("findings.json")
    except Exception as e:  # noqa: BLE001
        st.warning(f"Could not read findings.json from repo: {e}")
if findings_doc is None:
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "findings.json"), encoding="utf-8") as fh:
            findings_doc = json.load(fh)
    except Exception:  # noqa: BLE001
        findings_doc = {"markets": ["SE", "NO", "DK", "FI"], "category": "", "findings": []}

markets = findings_doc.get("markets") or ["SE", "NO", "DK", "FI"]

# --- state (status) in session ---
if "state" not in st.session_state:
    s, sha = ({}, None)
    if TOKEN:
        try:
            s, sha = gh_get("state.json")
            s = s or {}
        except Exception:  # noqa: BLE001
            s, sha = {}, None
    st.session_state.state = s
    st.session_state.state_sha = sha
STATE = st.session_state.state

# pivot findings -> one record per (category, brand)
brands = {}
for f in findings_doc.get("findings", []):
    catn = f.get("category") or findings_doc.get("category") or "-"
    key = f"{catn}␟{f['brand']}"
    b = brands.setdefault(key, {"id": key, "brand": f["brand"], "category": catn,
                                "base": {}, "note": "", "signal": "", "merchants": [],
                                "demand": set(), "typ": "Category up-and-comer"})
    b["base"][f["market"]] = "in" if f.get("in_cdon") else "gap"
    if f.get("demand"):
        b["demand"].add(f["market"])
    if f.get("typ"):
        b["typ"] = canon_typ(f["typ"])
    if f.get("signal") and not b["signal"]:
        b["signal"] = f["signal"]
    if not b["note"] and f.get("note"):
        b["note"] = f["note"]
    for m in f.get("merchants_selling", []):
        b["merchants"].append({**m, "market": f["market"]})


def cell_state(b, m):
    if b["base"].get(m) == "in":
        return "in"
    if b["base"].get(m) != "gap":
        return "na"
    return STATE.get(b["id"], {}).get("markets", {}).get(m, "gap")


def comment_of(b):
    return STATE.get(b["id"], {}).get("comment", "")


def set_status(bid, market, status):
    STATE.setdefault(bid, {"markets": {}, "comment": ""})["markets"][market] = status


def set_comment(bid, comment):
    STATE.setdefault(bid, {"markets": {}, "comment": ""})["comment"] = comment


def save_state(message):
    try:
        st.session_state.state_sha = gh_put("state.json", STATE, st.session_state.state_sha, message)
        gh_get.clear()
        st.toast("Saved to shared source (repo)")
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else None
        if code == 409:
            st.error("Status conflict - someone saved at the same time. Reloading, please try again.")
            del st.session_state["state"]; st.rerun()
        else:
            st.error(f"Could not save status: {e}")


# --- Sidebar ---
if st.sidebar.button("Refresh data", use_container_width=True,
                     help="Fetch the latest findings + status from the repo (see others' changes)"):
    gh_get.clear()
    st.session_state.pop("state", None)
    st.rerun()
st.sidebar.header("Filter")
view = st.sidebar.radio("View", ["Trends", "By merchant", "Market breadth"])
mkt = st.sidebar.selectbox("Market", ["All"] + markets)
cats = sorted({b["category"] for b in brands.values()})
cat = st.sidebar.selectbox("Category", ["All"] + cats)
q = st.sidebar.text_input("Search merchant")

st.title("GAP Assortment Radar")
mode = "read/write" if not READONLY else "read-only"
st.caption(f"Shared source (repo {REPO}, {mode}) · {len(brands)} brands · markets {', '.join(markets)}"
           + (f" · updated {findings_doc.get('updated')}" if findings_doc.get("updated") else ""))
if READONLY:
    st.warning("Read-only: no GAP_RADAR_PAT secret set. Add the secret GAP_RADAR_PAT in Streamlit "
               "(or env locally) to be able to save status to the repo.")


def trends_view():
    """Signal-first action list: the hottest rising gaps on top, with the route in.
    This is the real value — spot the trend, see how to act, update the assortment."""
    st.caption("Hottest trends on top. Each row is a brand that is missing from CDON in a market "
               "with rising demand. 'Route in' = an existing merchant that already sells it, or "
               "Merchant Acquisition. Handled (Live) items sink to the bottom.")
    vis = markets if mkt == "All" else [mkt]
    rows = []
    for b in brands.values():
        if cat != "All" and b["category"] != cat:
            continue
        for m in vis:
            if b["base"].get(m) != "gap" or m not in b.get("demand", set()):
                continue  # only GAP+trend (where it is BOTH missing and demand is rising)
            sellers = sorted({x["merchant"] for x in b["merchants"] if x["market"] == m})
            slabel = LABEL[cell_state(b, m)]
            rows.append({"id": b["id"], "Market_key": m,
                         "_rank": signal_rank(b.get("signal", "")),
                         "_done": 1 if slabel == "Live" else 0,
                         "Signal": b.get("signal", "") or "-", "Brand": b["brand"],
                         "Category": b["category"], "Market": m,
                         "Type": b.get("typ", "Category up-and-comer"),
                         "Route in": ", ".join(sellers) or ACQ,
                         "Status": slabel, "Comment": comment_of(b)})
    if not rows:
        st.info("No trending gaps for the selected filter.")
        return
    df = pd.DataFrame(rows).sort_values(
        ["_done", "_rank", "Brand"], ascending=[True, False, True]).reset_index(drop=True)
    c1, c2, c3 = st.columns(3)
    c1.metric("Trending gaps", len(df))
    c2.metric("Breakout", int((df["Signal"].str.lower() == "breakout").sum()))
    c3.metric("With ready merchant", int((df["Route in"] != ACQ).sum()))
    edited = st.data_editor(
        df, hide_index=True, use_container_width=True, key="trends", disabled=READONLY,
        column_config={
            "id": None, "Market_key": None, "_rank": None, "_done": None,
            "Signal": st.column_config.TextColumn("Signal", disabled=True,
                                                  help="Rising search trend; Breakout = strongest"),
            "Brand": st.column_config.TextColumn(disabled=True),
            "Category": st.column_config.TextColumn(disabled=True),
            "Market": st.column_config.TextColumn(disabled=True),
            "Type": st.column_config.TextColumn(disabled=True),
            "Route in": st.column_config.TextColumn("Route in", disabled=True,
                                                    help="Existing merchant that already sells it, or Merchant Acquisition"),
            "Status": st.column_config.SelectboxColumn("Status", options=list(LABEL.values()), required=True),
        },
    )
    if not READONLY:
        dirty = False
        for i in range(len(df)):
            o, n = df.iloc[i], edited.iloc[i]
            if _norm(n["Status"]) != _norm(o["Status"]):
                set_status(o["id"], o["Market_key"], INV[n["Status"]]); dirty = True
            if _norm(n["Comment"]) != _norm(o["Comment"]):
                set_comment(o["id"], _norm(n["Comment"])); dirty = True
        if dirty:
            save_state("status update"); st.rerun()
    st.download_button("Export All CSV",
                       df.drop(columns=["id", "Market_key", "_rank", "_done"]).to_csv(index=False).encode("utf-8"),
                       file_name=f"gap_radar_trends_{datetime.date.today()}.csv", mime="text/csv")


def merchant_view():
    groups = {}
    for b in brands.values():
        if cat != "All" and b["category"] != cat:
            continue
        for m in markets:
            if b["base"].get(m) != "gap" or m not in b.get("demand", set()):
                continue  # only GAP+trend in the work view
            sellers = [x for x in b["merchants"] if x["market"] == m]
            if sellers:
                for s in sellers:
                    groups.setdefault(s["merchant"], {"site": s.get("site", ""), "lines": []})["lines"].append((b, m))
            else:
                groups.setdefault(ACQ, {"site": "", "lines": []})["lines"].append((b, m))
    names = sorted(groups, key=lambda n: (n == ACQ, n.lower()))
    if q:
        names = [n for n in names if q.lower() in n.lower()]
    if not names:
        st.info("No merchant matches.")
        return
    for name in names:
        g = groups[name]
        with st.expander(f"{name}  ({len(g['lines'])})", expanded=bool(q)):
            if g["site"]:
                st.caption(g["site"])
            df = pd.DataFrame([{
                "id": b["id"], "Market_key": m,
                "Brand": b["brand"], "Type": b.get("typ", "Category up-and-comer"),
                "Category": b["category"], "Signal": b.get("signal", ""), "Market": m,
                "Status": LABEL[cell_state(b, m)], "Comment": comment_of(b),
            } for b, m in g["lines"]])
            # Editable status/comment PER ROW (per individual brand/market)
            edited = st.data_editor(
                df, hide_index=True, use_container_width=True, key="me_" + slug(name),
                disabled=READONLY,
                column_config={
                    "id": None, "Market_key": None,
                    "Status": st.column_config.SelectboxColumn("Status", options=list(LABEL.values()), required=True),
                    "Brand": st.column_config.TextColumn(disabled=True),
                    "Type": st.column_config.TextColumn(disabled=True),
                    "Category": st.column_config.TextColumn(disabled=True),
                    "Signal": st.column_config.TextColumn(disabled=True),
                    "Market": st.column_config.TextColumn(disabled=True),
                },
            )
            if not READONLY:
                dirty = False
                for i in range(len(df)):
                    o, n = df.iloc[i], edited.iloc[i]
                    if _norm(n["Status"]) != _norm(o["Status"]):
                        set_status(o["id"], o["Market_key"], INV[n["Status"]]); dirty = True
                    if _norm(n["Comment"]) != _norm(o["Comment"]):
                        set_comment(o["id"], _norm(n["Comment"])); dirty = True
                if dirty:
                    save_state(f"status: {name}"); st.rerun()
            c1, c2 = st.columns(2)
            if not READONLY and c1.button("Mark ALL as Contacted", key="k_" + slug(name)):
                for b, m in g["lines"]:
                    if cell_state(b, m) == "gap":
                        set_status(b["id"], m, "kontaktad")
                save_state(f"contacted all: {name}"); st.rerun()
            c2.download_button("Download Merchant",
                               df.drop(columns=["id", "Market_key"]).to_csv(index=False).encode("utf-8"),
                               file_name=f"gap_radar_{slug(name)}_{datetime.date.today()}.csv",
                               mime="text/csv", key="d_" + slug(name))


def breadth_cell(b, m):
    base = b["base"].get(m)
    if base == "in":
        return "in CDON"
    if base == "gap":
        return "GAP+trend" if m in b.get("demand", set()) else "GAP"
    return "-"


def breadth_view():
    st.caption("in CDON = in the assortment  ·  GAP+trend = missing + rising demand (act)  ·  "
               "GAP = missing but no trend yet (breadth potential)")
    rows = []
    for b in brands.values():
        if cat != "All" and b["category"] != cat:
            continue
        r = {"Brand": b["brand"], "Category": b["category"], "Signal": b.get("signal", "")}
        for m in markets:
            r[m] = breadth_cell(b, m)
        r["GAP+trend (count)"] = sum(1 for m in markets if r[m] == "GAP+trend")
        rows.append(r)
    if not rows:
        st.info("No brands for the selected filter.")
        return
    df = pd.DataFrame(rows).sort_values(
        ["GAP+trend (count)", "Brand"], ascending=[False, True]).reset_index(drop=True)
    c1, c2 = st.columns(2)
    c1.metric("Brands", len(df))
    c2.metric("Broad (GAP+trend in ≥2 markets)", int((df["GAP+trend (count)"] >= 2).sum()))
    st.dataframe(df, hide_index=True, use_container_width=True)
    st.download_button("Export market breadth CSV", df.to_csv(index=False).encode("utf-8"),
                       file_name=f"gap_radar_breadth_{datetime.date.today()}.csv", mime="text/csv")


if view == "Trends":
    trends_view()
elif view == "By merchant":
    merchant_view()
else:
    breadth_view()
