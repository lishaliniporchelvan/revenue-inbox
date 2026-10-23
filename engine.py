"""Revenue Intelligence Inbox - core engine (import, upsert, routing, metrics, export)."""
import json, hashlib, sqlite3, io, os, re, zipfile, datetime as dt
from zoneinfo import ZoneInfo
import pandas as pd

IST = ZoneInfo("Asia/Kolkata")
DB = os.environ.get("INBOX_DB", "inbox.db")

ROUTES = [
    "Prospect outreach / discovery",
    "Partner / referral channel",
    "Supplier / vendor to SpendNest",
    "Sales evaluation (demo / pricing)",
    "Existing customer - support & setup",
    "Existing customer - migration / retention",
    "Internal enablement",
    "Follow-up / voicemail",
    "No usable text",
]

# ---------------- DB ----------------
def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS records(
          src_type TEXT, src_id TEXT, record_ref TEXT, revision INT, batch_id TEXT, exported_at TEXT,
          payload TEXT, transcript TEXT, chash TEXT,
          route TEXT, secondary TEXT, flags TEXT, review INT, review_reason TEXT, evidence TEXT,
          manual_route TEXT, manual_note TEXT, updated_at TEXT,
          PRIMARY KEY(src_type, src_id));
        CREATE TABLE IF NOT EXISTS imports(
          id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, file TEXT, batch_id TEXT, revision INT,
          inserted INT, updated INT, duplicate INT, stale INT, manual_kept INT);
        CREATE TABLE IF NOT EXISTS log(
          id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, src_type TEXT, src_id TEXT, ref TEXT,
          action TEXT, detail TEXT);
        """)

def reset():
    if os.path.exists(DB):
        os.remove(DB)
    init()

def now():
    return dt.datetime.now(IST).isoformat(timespec="seconds")

def _log(c, rec, action, detail=""):
    c.execute("INSERT INTO log(ts,src_type,src_id,ref,action,detail) VALUES(?,?,?,?,?,?)",
              (now(), rec["source_type"], rec["source_id"], rec["record_ref"], action, detail))

# ---------------- text helpers ----------------
def get_text(rec):
    """Return (full_text, speakers, usable_flag, unavailable_reason)."""
    if rec["source_type"] == "call":
        t = (rec.get("payload") or {}).get("transcript")
        return (t or ""), [], bool(t and t.strip()), None
    tp = rec.get("transcript_payload") or {}
    turns = [u for u in (tp.get("turns") or []) if (u.get("text") or "").strip()]
    text = " ".join(u["text"] for u in turns)
    return text, sorted({u["speaker"] for u in turns}), bool(turns), tp.get("unavailable_reason")

def snippet(text, pat, width=110):
    m = re.search(pat, text, re.I)
    if not m:
        return ""
    s = max(0, m.start() - 30)
    return text[s:s + width].replace("\n", " ").strip()

# ---------------- routing ----------------
FLAG_RULES = {
    "Do-not-contact / consent complaint": r"didn't ask for this call|how i ended up on your list|where did you find my details",
    "Prospect declined / has existing solution": r"we already have|already very automated|not looking for anything outside|aren't looking",
    "Proposal or material requested": r"send (me )?(a |your |the )?(proposal|presentation|brief|an explanation)|could you send a proposal|message me .* proposal|send the details first|send the cards proposal|please send the information",
    "Follow-up date agreed": r"(call|speak|try you) (on )?(monday|tuesday)|follow-up next week|arrange a follow-up",
    "Customer-reported product defect": r"still the old description|old value carries forward|expired-token|cannot upload a document|not being accepted",
    "Migration / service-change risk": r"close the old programme|cards will both stop working|identity checks again",
    "Prepaid vs credit expectation gap": r"these are prepaid|no credit facility",
    "Internal approval pending (customer)": r"time with the cfo|discuss (the options )?internally|speak with the relevant people internally",
}

def route_record(rec):
    text, speakers, usable, reason = get_text(rec)
    p = rec.get("payload") or {}
    title = (p.get("title") or "").lower()
    t = text.lower()
    ev = []
    flags = []
    secondary = []
    review = 0
    rr = ""

    if not usable:
        route = "No usable text"
        if rec["source_type"] == "call":
            rr = ""  # background call, not supplied
            ev.append("transcript=null (background call, text not supplied)")
        else:
            ev.append(f"unavailable_reason: {reason}")
            r = (reason or "").lower()
            if "later assessment batch" in r:
                review, rr = 1, "Transcript delivered later; re-import the update batch"
            elif "nobody spoke" in r:
                review, rr = 1, "Recorded but silent: confirm whether the meeting actually happened"
            elif "not scheduled" in r or p.get("recording_status") == "not_scheduled":
                review, rr = 1, "No notetaker scheduled: evidence cannot be assessed; do not assume outcome"
            else:
                review, rr = 0, "Notetaker cancelled before meeting"
        return route, secondary, flags, review, rr, ev

    for name, pat in FLAG_RULES.items():
        if name.startswith("Prospect declined") and rec["source_type"] != "call":
            continue
        s = snippet(text, pat)
        if s:
            flags.append(name)
            ev.append(f"{name}: \"{s}\"")

    sp = " ".join(speakers).lower()
    if "supplier" in sp:
        route = "Supplier / vendor to SpendNest"
    elif "trainer" in t or "demo account as an administrator" in t:
        route = "Internal enablement"
    elif "migration" in t and "identity checks again" in t:
        route = "Existing customer - migration / retention"
    elif rec["source_type"] == "meeting" and re.search(r"invitation|save changes|reset invitation", t) and "customer" in sp:
        route = "Existing customer - support & setup"
    elif re.search(r"commission|partnership|partner with|clients coming through", t):
        route = "Partner / referral channel"
        if re.search(r"cold call|cold introduction", t):
            secondary.append("Prospect outreach / discovery")
    elif "customer" in sp and re.search(r"pricing|price|demonstration|prepaid", t):
        route = "Sales evaluation (demo / pricing)"
    elif re.search(r"cold call|introductory|cold introduction|forty seconds", t):
        route = "Prospect outreach / discovery"
    elif rec["source_type"] == "call" and p.get("observed_outcome") in ("voicemail", "ooo"):
        route = "Follow-up / voicemail"
    elif rec["source_type"] == "call" and re.search(r"follow(ing)? up|we discussed", t):
        route = "Follow-up / voicemail"
        review, rr = 1, "Weak match: confirm route"
    elif "customer" in sp:
        route = "Prospect outreach / discovery"
        review, rr = 1, "Fallback route (no strong rule matched): confirm"
    else:
        route = "Prospect outreach / discovery"
        review, rr = 1, "Fallback route: confirm"

    # call outcome label vs transcript conflicts (source-reported labels are not ground truth)
    if rec["source_type"] == "call":
        o = p.get("observed_outcome")
        if o == "follow_up" and re.search(r"i said i will contact you|if we ever decide|if there is anything to discuss", t):
            flags.append("Stored outcome may overstate interest")
            ev.append("observed_outcome=follow_up but text: \"" + snippet(text, r"i said i will contact you|if we ever decide|if there is anything to discuss") + "\"")
            review, rr = 1, "Stored label 'follow_up' conflicts with transcript"
        if o == "referred" and re.search(r"internally|aren't looking for anything", t) and "send a proposal" not in t:
            flags.append("Stored outcome may overstate interest")
            ev.append("observed_outcome=referred but text: \"" + snippet(text, r"aren't looking for anything outside|internally") + "\"")
            review, rr = 1, "Stored label 'referred' conflicts with transcript"
        if p.get("is_connected") and (p.get("speaker_count") or 0) <= 1 and not p.get("is_conversation"):
            flags.append("Connection flag disagrees with other fields")
    if rec["source_type"] == "meeting" and "Customer A" in speakers and "customer" in sp and route.startswith("Existing") is False:
        pass
    return route, secondary, flags, review, rr, ev

def attention(row):
    """A record needs attention if it is in review or carries a business-risk flag."""
    risk = {"Customer-reported product defect", "Migration / service-change risk",
            "Do-not-contact / consent complaint", "Prepaid vs credit expectation gap",
            "Proposal or material requested", "Follow-up date agreed"}
    return bool(row["review"]) or any(f in risk for f in json.loads(row["flags"] or "[]"))

# ---------------- import / upsert ----------------
def content_hash(rec):
    d = {"payload": rec.get("payload"), "transcript_payload": rec.get("transcript_payload")}
    return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16]

def diff_fields(old, new):
    out = []
    for k in sorted(set(old) | set(new)):
        if old.get(k) != new.get(k):
            out.append(k)
    return out

def import_batch(data, filename="upload.json"):
    init()
    res = dict(inserted=0, updated=0, duplicate=0, stale=0, manual_kept=0)
    rev = int(data.get("revision", 1))
    with conn() as c:
        for rec in data["records"]:
            key = (rec["source_type"], rec["source_id"])  # identity = (source_type, source_id)
            chash = content_hash(rec)
            route, sec, flags, review, rr, ev = route_record(rec)
            old = c.execute("SELECT * FROM records WHERE src_type=? AND src_id=?", key).fetchone()
            vals = (rec["record_ref"], rev, data.get("batch_id"), data.get("exported_at"),
                    json.dumps(rec.get("payload")), json.dumps(rec.get("transcript_payload")), chash,
                    route, json.dumps(sec), json.dumps(flags), review, rr, json.dumps(ev), now())
            if old is None:
                c.execute("""INSERT INTO records(src_type,src_id,record_ref,revision,batch_id,exported_at,payload,
                    transcript,chash,route,secondary,flags,review,review_reason,evidence,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", key + vals[:1] + vals[1:])
                _log(c, rec, "insert", f"{data.get('batch_id')} rev{rev}")
                res["inserted"] += 1
            elif old["chash"] == chash:
                _log(c, rec, "duplicate_skipped", f"{data.get('batch_id')} identical content")
                res["duplicate"] += 1
            elif rev < old["revision"]:
                _log(c, rec, "stale_ignored", f"rev{rev} < stored rev{old['revision']}")
                res["stale"] += 1
            else:
                changed = []
                op, np_ = json.loads(old["payload"] or "null") or {}, rec.get("payload") or {}
                changed += ["payload." + k for k in diff_fields(op, np_)]
                ot = json.loads(old["transcript"] or "null") or {}
                nt = rec.get("transcript_payload") or {}
                if len(ot.get("turns") or []) != len(nt.get("turns") or []):
                    changed.append(f"transcript.turns {len(ot.get('turns') or [])}->{len(nt.get('turns') or [])}")
                c.execute("""UPDATE records SET record_ref=?,revision=?,batch_id=?,exported_at=?,payload=?,transcript=?,
                    chash=?,route=?,secondary=?,flags=?,review=?,review_reason=?,evidence=?,updated_at=?
                    WHERE src_type=? AND src_id=?""", vals + key)
                _log(c, rec, "update", f"rev{old['revision']}->rev{rev}; changed: {', '.join(changed)}; route {old['route']} -> {route}")
                res["updated"] += 1
                if old["manual_route"]:
                    res["manual_kept"] += 1
                    _log(c, rec, "manual_kept", f"manual route '{old['manual_route']}' kept over auto '{route}'")
        c.execute("INSERT INTO imports(ts,file,batch_id,revision,inserted,updated,duplicate,stale,manual_kept) VALUES(?,?,?,?,?,?,?,?,?)",
                  (now(), filename, data.get("batch_id"), rev, res["inserted"], res["updated"], res["duplicate"], res["stale"], res["manual_kept"]))
    return res

def set_manual(src_type, src_id, route, note):
    with conn() as c:
        old = c.execute("SELECT * FROM records WHERE src_type=? AND src_id=?", (src_type, src_id)).fetchone()
        c.execute("UPDATE records SET manual_route=?, manual_note=?, review=0 WHERE src_type=? AND src_id=?",
                  (route or None, note or None, src_type, src_id))
        c.execute("INSERT INTO log(ts,src_type,src_id,ref,action,detail) VALUES(?,?,?,?,?,?)",
                  (now(), src_type, src_id, old["record_ref"], "manual_correction" if route else "manual_reset",
                   f"{old['manual_route'] or old['route']} -> {route or old['route']}: {note or ''}"))

# ---------------- load / metrics ----------------
def load():
    init()
    with conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM records")]
    out = []
    for r in rows:
        p = json.loads(r["payload"]) or {}
        tp = json.loads(r["transcript"] or "null") or {}
        ts = p.get("started_at") if r["src_type"] == "call" else p.get("starts_at")
        t_ist = pd.Timestamp(ts).tz_convert(IST) if ts else None
        _, speakers, usable, reason = get_text({"source_type": r["src_type"], "payload": p, "transcript_payload": tp})
        out.append({
            "ref": r["record_ref"], "type": r["src_type"], "id": r["src_id"], "revision": r["revision"],
            "date_ist": t_ist.date().isoformat() if t_ist is not None else None,
            "time_ist": t_ist.strftime("%H:%M") if t_ist is not None else None,
            "owner": p.get("rep_name") or p.get("organizer_email"),
            "title": p.get("title"), "direction": p.get("direction"),
            "duration_s": p.get("duration_seconds"),
            "is_connected": p.get("is_connected"), "observed_outcome": p.get("observed_outcome"),
            "recording_status": p.get("recording_status"),
            "usable_text": usable, "unavailable_reason": reason,
            "auto_route": r["route"], "manual_route": r["manual_route"],
            "route": r["manual_route"] or r["route"],
            "secondary": ", ".join(json.loads(r["secondary"] or "[]")),
            "flags": " | ".join(json.loads(r["flags"] or "[]")),
            "review": bool(r["review"]), "review_reason": r["review_reason"],
            "evidence": " || ".join(json.loads(r["evidence"] or "[]")),
            "manual_note": r["manual_note"],
            "needs_attention": attention(r),
        })
    cols = ["ref","type","id","revision","date_ist","time_ist","owner","title","direction","duration_s","is_connected",
            "observed_outcome","recording_status","usable_text","unavailable_reason","auto_route","manual_route","route",
            "secondary","flags","review","review_reason","evidence","manual_note","needs_attention"]
    return add_associations(pd.DataFrame(out, columns=cols))

def account_label(title):
    if not isinstance(title, str) or not title:
        return None
    t = re.sub(r"^SpendNest (setup|product demonstration) - ", "", title)
    t = re.sub(r"^SpendNest <> ", "", t)
    t = re.split(r" / | - ", t)[0].strip()
    return t if t and "SpendNest" not in t and t.lower() not in ("account setup and workflow discussion", "billing workflow walkthrough", "crm demonstration", "internal design discussion") else None

def add_associations(df):
    df["account_label"] = df["title"].map(account_label)
    notes = []
    for i, r in df.iterrows():
        lab = r["account_label"]
        n = ""
        if lab:
            others = df[(df.account_label == lab) & (df.ref != r["ref"])]
            if len(others):
                n = "Proposed (unconfirmed) link to " + ", ".join(others.ref) + " - same account label in title"
        notes.append(n)
    df["proposed_association"] = notes
    return df

def metrics(df):
    m = {}
    m["selected_records"] = len(df)
    m["calls"] = int((df.type == "call").sum())
    m["meetings"] = int((df.type == "meeting").sum())
    m["usable_text"] = int(df.usable_text.sum())
    m["coverage_pct"] = float(round(100 * df.usable_text.mean(), 1)) if len(df) else 0
    calls = df[df.type == "call"]
    known = calls[calls.is_connected.notna()]
    m["connected_true"] = int((known.is_connected == True).sum())
    m["connected_denominator"] = len(known)
    m["connected_unknown"] = int(calls.is_connected.isna().sum())
    m["connect_rate_pct"] = round(100 * m["connected_true"] / m["connected_denominator"], 1) if len(known) else None
    m["needs_attention"] = int(df.needs_attention.sum())
    m["in_review"] = int(df.review.sum())
    m["route_assignments"] = int(len(df) + (df.secondary != "").sum())
    return m

# ---------------- export ----------------
def build_exports(outdir="exports"):
    df = load()
    os.makedirs(outdir, exist_ok=True)
    files = {}
    cols = ["ref", "type", "id", "revision", "date_ist", "time_ist", "owner", "title", "account_label", "proposed_association", "route", "secondary",
            "flags", "review", "review_reason", "needs_attention", "usable_text", "observed_outcome",
            "is_connected", "recording_status", "evidence", "manual_route", "manual_note"]
    files["all_records.csv"] = df[cols]
    files["review_queue.csv"] = df[df.review][cols]
    files["needs_attention.csv"] = df[df.needs_attention][cols]
    for r in ROUTES:
        sub = df[df.route == r]
        if len(sub):
            slug = re.sub(r"[^a-z0-9]+", "_", r.lower()).strip("_")
            files[f"routes/{slug}.csv"] = sub[cols]
    with conn() as c:
        files["audit_log.csv"] = pd.read_sql("SELECT * FROM log ORDER BY id", c)
        files["import_history.csv"] = pd.read_sql("SELECT * FROM imports ORDER BY id", c)
    manifest = {"generated_at": now(), "timezone": "Asia/Kolkata",
                "note": "Counts are selected sample records, not a whole-team day.",
                "metrics": metrics(df), "files": []}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, f in files.items():
            data = f.to_csv(index=False).encode()
            path = os.path.join(outdir, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "wb").write(data)
            manifest["files"].append({"file": name, "rows": len(f), "sha256": hashlib.sha256(data).hexdigest()})
            z.writestr(name, data)
        mj = json.dumps(manifest, indent=2, default=str).encode()
        open(os.path.join(outdir, "manifest.json"), "wb").write(mj)
        z.writestr("manifest.json", mj)
    return manifest, buf.getvalue()
