#!/usr/bin/env python3
"""GAP Assortment Radar - Streamlit-app (repo-backad läs/skriv).

Läser findings.json (gaps, pushas av Aurora) + state.json (status) från GitHub-repot
johanna-stack/gap-assortment-radar, och skriver status tillbaka till state.json via
GitHub Contents-API:t med GAP_RADAR_PAT. Ingen backend/Mac behövs.

VIKTIGT: GAP_RADAR_PAT är scopad till BARA detta repo. cdon-trackers rörs aldrig.

Lokalt:  export GAP_RADAR_PAT=...   &&  streamlit run streamlit_app.py
Cloud:   sätt secret  GAP_RADAR_PAT = "github_pat_..."
Utan token: read-only mot bundlad findings.json (status kan inte sparas).
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
LABEL = {"gap": "GAP", "kontaktad": "Kontaktad", "avvakta": "Avvakta", "live": "Live"}
INV = {v: k for k, v in LABEL.items()}
GROUP_OF = {"GAP": "NY", "Kontaktad": "Kontaktad", "Avvakta": "Avvakta", "Live": "Klara"}
STATUS_GROUPS = ["NY", "Kontaktad", "Avvakta", "Klara"]

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
    """Normalisera cellvärde för jämförelse: NaN/None -> "". Annars hade tomma
    Kommentar-celler (NaN i data_editor) alltid skiljt sig från "" och trigga en
    oändlig save->rerun-loop."""
    if x is None:
        return ""
    try:
        if pd.isna(x):
            return ""
    except (TypeError, ValueError):
        pass
    return str(x).strip()


# --- findings (gaps) ---
findings_doc = None
if TOKEN:
    try:
        findings_doc, _ = gh_get("findings.json")
    except Exception as e:  # noqa: BLE001
        st.warning(f"Kunde inte läsa findings.json från repo: {e}")
if findings_doc is None:
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "findings.json"), encoding="utf-8") as fh:
            findings_doc = json.load(fh)
    except Exception:  # noqa: BLE001
        findings_doc = {"markets": ["SE", "NO", "DK", "FI"], "category": "", "findings": []}

markets = findings_doc.get("markets") or ["SE", "NO", "DK", "FI"]

# --- state (status) i session ---
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

# pivotera findings -> en post per (kategori, brand)
brands = {}
for f in findings_doc.get("findings", []):
    catn = f.get("category") or findings_doc.get("category") or "-"
    key = f"{catn}␟{f['brand']}"
    b = brands.setdefault(key, {"id": key, "brand": f["brand"], "category": catn,
                                "base": {}, "note": "", "signal": "", "merchants": [],
                                "demand": set(), "typ": "Kategori-uppstickare"})
    b["base"][f["market"]] = "in" if f.get("in_cdon") else "gap"
    if f.get("demand"):
        b["demand"].add(f["market"])
    if f.get("typ"):
        b["typ"] = f["typ"]
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
        st.toast("Sparat till delad källa (repo)")
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else None
        if code == 409:
            st.error("Statuskonflikt - någon sparade samtidigt. Laddar om, försök igen.")
            del st.session_state["state"]; st.rerun()
        else:
            st.error(f"Kunde inte spara status: {e}")


# --- Sidofält ---
if st.sidebar.button("Uppdatera data", use_container_width=True,
                     help="Hämta senaste findings + status från repot (ser andras ändringar)"):
    gh_get.clear()
    st.session_state.pop("state", None)
    st.rerun()
st.sidebar.header("Filter")
view = st.sidebar.radio("Vy", ["Per marknad", "Per merchant", "Marknadsbredd"])
mkt = st.sidebar.selectbox("Marknad", ["Alla"] + markets)
cats = sorted({b["category"] for b in brands.values()})
cat = st.sidebar.selectbox("Kategori", ["Alla"] + cats)
q = st.sidebar.text_input("Sök merchant")

st.title("GAP Assortment Radar")
mode = "läs/skriv" if not READONLY else "read-only"
st.caption(f"Delad källa (repo {REPO}, {mode}) · {len(brands)} brands · marknader {', '.join(markets)}"
           + (f" · uppdaterad {findings_doc.get('updated')}" if findings_doc.get("updated") else ""))
if READONLY:
    st.warning("Read-only: ingen GAP_RADAR_PAT-secret satt. Lägg secret GAP_RADAR_PAT i Streamlit "
               "(eller env lokalt) för att kunna spara status till repot.")


def _market_section(df, key):
    edited = st.data_editor(
        df, hide_index=True, use_container_width=True, key=key, disabled=READONLY,
        column_config={
            "id": None, "Typ": None, "Grupp": None,
            "Status": st.column_config.SelectboxColumn("Status", options=list(LABEL.values()), required=True),
            "Brand": st.column_config.TextColumn(disabled=True),
            "Kategori": st.column_config.TextColumn(disabled=True),
            "Signal": st.column_config.TextColumn("Signal", disabled=True, help="Trend (rising search volume)"),
            "Marknad": st.column_config.TextColumn(disabled=True),
            "Merchant": st.column_config.TextColumn(disabled=True),
        },
    )
    if READONLY:
        return
    dirty = False
    for i in range(len(df)):
        o, n = df.iloc[i], edited.iloc[i]
        if _norm(n["Status"]) != _norm(o["Status"]):
            set_status(o["id"], o["Marknad"], INV[n["Status"]]); dirty = True
        if _norm(n["Kommentar"]) != _norm(o["Kommentar"]):
            set_comment(o["id"], _norm(n["Kommentar"])); dirty = True
    if dirty:
        save_state("status-uppdatering"); st.rerun()


def market_view():
    vis = markets if mkt == "Alla" else [mkt]
    rows = []
    for b in brands.values():
        if cat != "Alla" and b["category"] != cat:
            continue
        for m in vis:
            if b["base"].get(m) != "gap" or m not in b.get("demand", set()):
                continue  # arbetsvyn visar bara GAP+trend (demand) — bredden finns i Marknadsbredd-vyn
            sellers = sorted({x["merchant"] for x in b["merchants"] if x["market"] == m})
            slabel = LABEL[cell_state(b, m)]
            rows.append({"id": b["id"], "Brand": b["brand"], "Kategori": b["category"],
                         "Signal": b.get("signal", ""), "Marknad": m,
                         "Typ": b.get("typ", "Kategori-uppstickare"), "Grupp": GROUP_OF[slabel],
                         "Status": slabel, "Merchant": ", ".join(sellers) or ACQ,
                         "Kommentar": comment_of(b)})
    if not rows:
        st.info("Inga GAP för valt filter.")
        return
    df = pd.DataFrame(rows)
    c1, c2, c3 = st.columns(3)
    c1.metric("GAP-rader", len(df))
    c2.metric("Live", int((df["Status"] == "Live").sum()))
    c3.metric("Brands", df["Brand"].nunique())
    # Separata sektioner per typ i samma vy
    order = ["Kategori-uppstickare", "Peak-modell"]
    present = [t for t in order if (df["Typ"] == t).any()]
    present += [t for t in sorted(df["Typ"].unique()) if t not in order]
    for t in present:
        sub_t = df[df["Typ"] == t]
        st.subheader(f"{t}  ({len(sub_t)})")
        # separata status-grupper inom varje typ-sektion
        for grp in STATUS_GROUPS:
            g = sub_t[sub_t["Grupp"] == grp].reset_index(drop=True)
            if g.empty:
                continue
            st.markdown(f"**{grp}** ({len(g)})")
            _market_section(g, key=f"ed_{slug(t)}_{grp}")
    st.download_button("Exportera Allt CSV",
                       df.drop(columns=["id"]).to_csv(index=False).encode("utf-8"),
                       file_name=f"gap_radar_{datetime.date.today()}.csv", mime="text/csv")


def merchant_view():
    groups = {}
    for b in brands.values():
        if cat != "Alla" and b["category"] != cat:
            continue
        for m in markets:
            if b["base"].get(m) != "gap" or m not in b.get("demand", set()):
                continue  # bara GAP+trend i arbetsvyn
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
        st.info("Ingen merchant matchar.")
        return
    for name in names:
        g = groups[name]
        with st.expander(f"{name}  ({len(g['lines'])})", expanded=bool(q)):
            if g["site"]:
                st.caption(g["site"])
            df = pd.DataFrame([{
                "id": b["id"], "Marknad_key": m,
                "Brand": b["brand"], "Typ": b.get("typ", "Kategori-uppstickare"),
                "Kategori": b["category"], "Signal": b.get("signal", ""), "Marknad": m,
                "Status": LABEL[cell_state(b, m)], "Kommentar": comment_of(b),
            } for b, m in g["lines"]])
            # Editerbar status/kommentar PER RAD (per enskild brand/marknad)
            edited = st.data_editor(
                df, hide_index=True, use_container_width=True, key="me_" + slug(name),
                disabled=READONLY,
                column_config={
                    "id": None, "Marknad_key": None,
                    "Status": st.column_config.SelectboxColumn("Status", options=list(LABEL.values()), required=True),
                    "Brand": st.column_config.TextColumn(disabled=True),
                    "Typ": st.column_config.TextColumn(disabled=True),
                    "Kategori": st.column_config.TextColumn(disabled=True),
                    "Signal": st.column_config.TextColumn(disabled=True),
                    "Marknad": st.column_config.TextColumn(disabled=True),
                },
            )
            if not READONLY:
                dirty = False
                for i in range(len(df)):
                    o, n = df.iloc[i], edited.iloc[i]
                    if _norm(n["Status"]) != _norm(o["Status"]):
                        set_status(o["id"], o["Marknad_key"], INV[n["Status"]]); dirty = True
                    if _norm(n["Kommentar"]) != _norm(o["Kommentar"]):
                        set_comment(o["id"], _norm(n["Kommentar"])); dirty = True
                if dirty:
                    save_state(f"status: {name}"); st.rerun()
            c1, c2 = st.columns(2)
            if not READONLY and c1.button("Markera ALLA som Kontaktad", key="k_" + slug(name)):
                for b, m in g["lines"]:
                    if cell_state(b, m) == "gap":
                        set_status(b["id"], m, "kontaktad")
                save_state(f"kontaktad alla: {name}"); st.rerun()
            c2.download_button("Ladda ned Merchant",
                               df.drop(columns=["id", "Marknad_key"]).to_csv(index=False).encode("utf-8"),
                               file_name=f"gap_radar_{slug(name)}_{datetime.date.today()}.csv",
                               mime="text/csv", key="d_" + slug(name))


def breadth_cell(b, m):
    base = b["base"].get(m)
    if base == "in":
        return "finns"
    if base == "gap":
        return "GAP+trend" if m in b.get("demand", set()) else "GAP"
    return "-"


def breadth_view():
    st.caption("finns = i CDON  ·  GAP+trend = saknas + stigande efterfrågan (agera)  ·  "
               "GAP = saknas men ingen trend ännu (bredd-potential)")
    rows = []
    for b in brands.values():
        if cat != "Alla" and b["category"] != cat:
            continue
        r = {"Brand": b["brand"], "Kategori": b["category"], "Signal": b.get("signal", "")}
        for m in markets:
            r[m] = breadth_cell(b, m)
        r["GAP+trend (antal)"] = sum(1 for m in markets if r[m] == "GAP+trend")
        rows.append(r)
    if not rows:
        st.info("Inga brands för valt filter.")
        return
    df = pd.DataFrame(rows).sort_values(
        ["GAP+trend (antal)", "Brand"], ascending=[False, True]).reset_index(drop=True)
    c1, c2 = st.columns(2)
    c1.metric("Brands", len(df))
    c2.metric("Brett (GAP+trend i ≥2 marknader)", int((df["GAP+trend (antal)"] >= 2).sum()))
    st.dataframe(df, hide_index=True, use_container_width=True)
    st.download_button("Exportera marknadsbredd CSV", df.to_csv(index=False).encode("utf-8"),
                       file_name=f"gap_radar_bredd_{datetime.date.today()}.csv", mime="text/csv")


if view == "Per marknad":
    market_view()
elif view == "Per merchant":
    merchant_view()
else:
    breadth_view()
