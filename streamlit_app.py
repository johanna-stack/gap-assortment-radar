#!/usr/bin/env python3
"""GAP Assortment Radar - Streamlit-app.

Speglar HTML-dashboarden men i Streamlit. Läser och SKRIVER mot Aurora-backend
(/api/gap-radar) så status + audit delas med HTML-vyn. Backend-URL via AURORA_URL.

Kör lokalt:
    pip install streamlit requests pandas
    export AURORA_URL=http://127.0.0.1:5174        # default
    streamlit run streamlit_app.py

Deploy (Streamlit Cloud): sätt AURORA_URL till en nåbar backend (ej localhost).
"""
import os
import re
import datetime

import requests
import pandas as pd
import streamlit as st

AURORA = os.environ.get("AURORA_URL", "http://127.0.0.1:5174").rstrip("/")
ACQ = "Merchant Acquisition"
LABEL = {"gap": "GAP", "kontaktad": "Kontaktad", "live": "Live"}
INV = {v: k for k, v in LABEL.items()}

st.set_page_config(page_title="GAP Assortment Radar", layout="wide")


@st.cache_data(ttl=10)
def fetch():
    r = requests.get(f"{AURORA}/api/gap-radar", timeout=20)
    r.raise_for_status()
    return r.json()


def post_state(bid, market=None, status=None, comment=None):
    if READONLY:
        return
    body = {"id": bid}
    if market and status:
        body.update(market=market, status=status)
    if comment is not None:
        body["comment"] = comment
    requests.post(f"{AURORA}/api/gap-radar/state", json=body, timeout=20).raise_for_status()


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "x").lower()).strip("-")[:40]


import json as _json
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "findings.json")
READONLY = False
try:
    doc = fetch()
except Exception:  # noqa: BLE001
    # Fallback: bundlad findings.json (read-only — ingen delad state/skrivning)
    READONLY = True
    try:
        with open(DATA_FILE, encoding="utf-8") as fh:
            local = _json.load(fh)
    except Exception as e:  # noqa: BLE001
        st.title("GAP Assortment Radar")
        st.error(f"Aurora-backend ej nåbar ({AURORA}) och ingen lokal findings.json: {e}")
        st.stop()
    doc = {"markets": local.get("markets", ["SE", "NO", "DK", "FI"]),
           "category": local.get("category", ""),
           "findings": local.get("findings", []),
           "state": local.get("state", {})}

markets = doc.get("markets") or ["SE", "NO", "DK", "FI"]
state = doc.get("state") or {}

# pivotera findings -> en post per (kategori, brand)
brands = {}
for f in doc.get("findings", []):
    catn = f.get("category") or doc.get("category") or "-"
    key = f"{catn}␟{f['brand']}"
    b = brands.setdefault(key, {"id": key, "brand": f["brand"], "category": catn,
                                "base": {}, "note": "", "merchants": []})
    b["base"][f["market"]] = "in" if f.get("in_cdon") else "gap"
    if not b["note"] and f.get("note"):
        b["note"] = f["note"]
    for m in f.get("merchants_selling", []):
        b["merchants"].append({**m, "market": f["market"]})


def cell_state(b, m):
    if b["base"].get(m) == "in":
        return "in"
    if b["base"].get(m) != "gap":
        return "na"
    return state.get(b["id"], {}).get("markets", {}).get(m, "gap")


def comment_of(b):
    return state.get(b["id"], {}).get("comment", "")


# --- Sidofält / filter ---
st.sidebar.header("Filter")
view = st.sidebar.radio("Vy", ["Per marknad", "Per merchant"])
mkt = st.sidebar.selectbox("Marknad", ["Alla"] + markets)
cats = sorted({b["category"] for b in brands.values()})
cat = st.sidebar.selectbox("Kategori", ["Alla"] + cats)
q = st.sidebar.text_input("Sök merchant")

st.title("GAP Assortment Radar")
st.caption(f"{len(brands)} brands · marknader {', '.join(markets)} · källa "
           + ("bundlad findings.json (read-only)" if READONLY else AURORA)
           + (f" · uppdaterad {doc.get('updated')}" if doc.get("updated") else ""))
if READONLY:
    st.warning("Read-only: Aurora-backend ej nåbar. Status sparas inte. "
               "Sätt AURORA_URL (Streamlit secret) till en nåbar backend för läs/skriv.")


def market_view():
    vis = markets if mkt == "Alla" else [mkt]
    rows = []
    for b in brands.values():
        if cat != "Alla" and b["category"] != cat:
            continue
        for m in vis:
            if b["base"].get(m) != "gap":
                continue
            sellers = sorted({x["merchant"] for x in b["merchants"] if x["market"] == m})
            rows.append({
                "id": b["id"], "Brand": b["brand"], "Kategori": b["category"], "Marknad": m,
                "Status": LABEL[cell_state(b, m)],
                "Merchant": ", ".join(sellers) or ACQ,
                "Kommentar": comment_of(b),
            })
    if not rows:
        st.info("Inga GAP för valt filter.")
        return
    df = pd.DataFrame(rows)
    c1, c2, c3 = st.columns(3)
    c1.metric("GAP-rader", len(df))
    c2.metric("Live", int((df["Status"] == "Live").sum()))
    c3.metric("Brands", df["Brand"].nunique())

    edited = st.data_editor(
        df, hide_index=True, use_container_width=True, key="ed_market", disabled=READONLY,
        column_config={
            "id": None,
            "Status": st.column_config.SelectboxColumn("Status", options=list(LABEL.values()), required=True),
            "Brand": st.column_config.TextColumn(disabled=True),
            "Kategori": st.column_config.TextColumn(disabled=True),
            "Marknad": st.column_config.TextColumn(disabled=True),
            "Merchant": st.column_config.TextColumn(disabled=True),
        },
    )
    changes = 0
    for i in ([] if READONLY else range(len(df))):
        o, n = df.iloc[i], edited.iloc[i]
        if n["Status"] != o["Status"]:
            post_state(o["id"], market=o["Marknad"], status=INV[n["Status"]]); changes += 1
        if n["Kommentar"] != o["Kommentar"]:
            post_state(o["id"], comment=n["Kommentar"]); changes += 1
    if changes:
        st.cache_data.clear(); st.rerun()

    st.download_button("Exportera Allt CSV",
                       df.drop(columns=["id"]).to_csv(index=False).encode("utf-8"),
                       file_name=f"gap_radar_{datetime.date.today()}.csv", mime="text/csv")


def merchant_view():
    groups = {}
    for b in brands.values():
        if cat != "Alla" and b["category"] != cat:
            continue
        for m in markets:
            if b["base"].get(m) != "gap":
                continue
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
                "Brand": b["brand"], "Kategori": b["category"], "Marknad": m,
                "Status": LABEL[cell_state(b, m)], "Kommentar": comment_of(b),
            } for b, m in g["lines"]])
            st.dataframe(df, hide_index=True, use_container_width=True)
            c1, c2 = st.columns(2)
            if not READONLY and c1.button("Markera alla Kontaktad", key="k_" + slug(name)):
                for b, m in g["lines"]:
                    if cell_state(b, m) == "gap":
                        post_state(b["id"], market=m, status="kontaktad")
                st.cache_data.clear(); st.rerun()
            c2.download_button("Ladda ned Merchant", df.to_csv(index=False).encode("utf-8"),
                               file_name=f"gap_radar_{slug(name)}_{datetime.date.today()}.csv",
                               mime="text/csv", key="d_" + slug(name))


if view == "Per marknad":
    market_view()
else:
    merchant_view()
