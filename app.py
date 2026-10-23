import json, os
import pandas as pd
import streamlit as st
import engine as e

st.set_page_config(page_title="SpendNest Revenue Intelligence Inbox", layout="wide")
e.init()
st.title("Revenue Intelligence Inbox")
st.caption("Selected sample records (21 Aug 2026, Asia/Kolkata). Counts describe this package, not a whole-team day.")

tab_imp, tab_dash, tab_inbox, tab_review, tab_exp, tab_log = st.tabs(
    ["Import", "Dashboard", "Inbox", "Review queue", "Exports", "Audit log"])

# ---------------- Import ----------------
with tab_imp:
    st.write("Import delivery files (JSON). Identity = (source_type, source_id). Re-importing identical content is skipped; "
             "a higher revision with changed content updates the record; manual corrections are kept.")
    c1, c2 = st.columns(2)
    for col, fname in ((c1, "01_initial.json"), (c2, "02_update.json")):
        path = os.path.join("data", fname)
        if os.path.exists(path) and col.button(f"Import bundled {fname}"):
            res = e.import_batch(json.load(open(path)), fname)
            col.success(res)
    up = st.file_uploader("Or upload a delivery JSON", type=["json"])
    if up and st.button("Import uploaded file", type="primary"):
        try:
            data = json.load(up)
            assert "records" in data
            st.success(e.import_batch(data, up.name))
        except Exception as ex:
            st.error(f"Could not import: {ex}")
    if st.button("Reset database"):
        e.reset()
        st.rerun()
    with e.conn() as c:
        st.subheader("Import history")
        st.dataframe(pd.read_sql("SELECT * FROM imports ORDER BY id DESC", c), width='stretch')

df = e.load()

# ---------------- Dashboard ----------------
with tab_dash:
    if df.empty:
        st.info("Import data first.")
    else:
        m = e.metrics(df)
        a, b, c3, d, f = st.columns(5)
        a.metric("Selected records", m["selected_records"], f"{m['calls']} calls / {m['meetings']} meetings")
        b.metric("Usable text coverage", f"{m['coverage_pct']}%", f"{m['usable_text']} of {m['selected_records']}")
        c3.metric("Source-reported connect rate", f"{m['connect_rate_pct']}%",
                  f"{m['connected_true']}/{m['connected_denominator']} calls, {m['connected_unknown']} unknown")
        d.metric("Needs attention", m["needs_attention"])
        f.metric("In review queue", m["in_review"])
        st.caption(f"Route assignments: {m['route_assignments']} vs unique records: {m['selected_records']} "
                   "(difference = records with a secondary route).")
        l, r = st.columns(2)
        l.subheader("Records by route")
        l.bar_chart(df.route.value_counts())
        r.subheader("Records by owner")
        r.bar_chart(df.owner.value_counts())
        l2, r2 = st.columns(2)
        calls = df[df.type == "call"]
        l2.subheader("Call outcomes (stored label)")
        l2.bar_chart(calls.observed_outcome.value_counts())
        r2.subheader("Meeting capture state")
        r2.bar_chart(df[df.type == "meeting"].recording_status.value_counts())
        st.subheader("Needs attention")
        st.dataframe(df[df.needs_attention][["ref", "type", "owner", "title", "route", "flags", "review_reason"]],
                     width='stretch')

# ---------------- Inbox ----------------
with tab_inbox:
    if df.empty:
        st.info("Import data first.")
    else:
        rf = st.multiselect("Route", e.ROUTES, default=[r for r in e.ROUTES if r != "No usable text"])
        tf = st.multiselect("Type", ["call", "meeting"], default=["call", "meeting"])
        only = st.checkbox("Only records needing attention")
        v = df[df.route.isin(rf) & df.type.isin(tf)]
        if only:
            v = v[v.needs_attention]
        st.dataframe(v[["ref", "type", "time_ist", "owner", "title", "route", "secondary", "flags", "review", "manual_route"]],
                     width='stretch')
        if len(v):
            ref = st.selectbox("Open record", v.ref.tolist())
            row = df[df.ref == ref].iloc[0]
            st.markdown(f"**{row['title'] or row['owner']}**  |  route: `{row['route']}`  |  revision {row['revision']}")
            if row["proposed_association"]:
                st.warning(row["proposed_association"])
            if row["review_reason"]:
                st.warning("Review: " + row["review_reason"])
            st.write("**Evidence (source quotes behind flags)**")
            for ev in (row["evidence"] or "").split(" || "):
                if ev:
                    st.write("- " + ev)
            st.subheader("Manual correction")
            idx = e.ROUTES.index(row["route"]) if row["route"] in e.ROUTES else 0
            nr = st.selectbox("Correct route", e.ROUTES, index=idx, key="nr_" + ref)
            note = st.text_input("Why", row["manual_note"] or "", key="note_" + ref)
            c1, c2 = st.columns(2)
            if c1.button("Save correction"):
                e.set_manual(row["type"], row["id"], nr, note)
                st.rerun()
            if c2.button("Reset to automatic"):
                e.set_manual(row["type"], row["id"], None, None)
                st.rerun()

# ---------------- Review queue ----------------
with tab_review:
    if df.empty:
        st.info("Import data first.")
    else:
        rq = df[df.review]
        st.write(f"{len(rq)} records need a person to resolve uncertainty or missing information.")
        st.dataframe(rq[["ref", "type", "title", "route", "review_reason", "proposed_association"]], width='stretch')

# ---------------- Exports ----------------
with tab_exp:
    if df.empty:
        st.info("Import data first.")
    else:
        if st.button("Generate exports", type="primary"):
            manifest, z = e.build_exports()
            st.session_state["zip"] = z
            st.json(manifest)
        if "zip" in st.session_state:
            st.download_button("Download exports.zip", st.session_state["zip"], "exports.zip")

# ---------------- Audit log ----------------
with tab_log:
    with e.conn() as c:
        st.dataframe(pd.read_sql("SELECT * FROM log ORDER BY id DESC", c), width='stretch')
