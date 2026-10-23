# SpendNest Revenue Intelligence Inbox

Streamlit app for a sales manager to review selected call and meeting records, see what needs attention, and export organized data.

## Run
```
pip install -r requirements.txt
streamlit run app.py
```
Open the Import tab, click "Import bundled 01_initial.json", then "Import bundled 02_update.json".

## What it does
- **Import / upsert**: identity is `(source_type, source_id)`. Identical content = skipped (duplicate). Higher revision with changed content = updated and logged with changed fields. Lower revision = ignored as stale. Similar titles are never used as a key.
- **Routing** (rule-based, explainable): Prospect outreach, Partner/referral, Supplier, Sales evaluation, Existing customer support, Existing customer migration, Internal enablement, Follow-up/voicemail, No usable text. A record can have a secondary route; the dashboard shows route assignments vs unique records (45 vs 44).
- **Attention flags** with the source quote behind each (consent complaint, proposal requested, product defect, migration risk, prepaid-vs-credit gap, stored label conflicts transcript).
- **Review queue**: records where a person must resolve uncertainty (silent meetings, no capture, label conflicts, weak routes).
- **Manual corrections**: saved with a reason, kept over automatic routing, and logged.
- **Dashboard** (all counts labelled as selected records, Asia/Kolkata): coverage = usable texts / records; source-reported connect rate with denominator and unknown count; routes, owners, outcomes, capture state.
- **Exports**: all_records, review_queue, needs_attention, one CSV per route, audit_log, import_history, and `manifest.json` (rows + SHA-256). Pre-generated copy is in `exports/`.

## Duplicate / update test (reproduced in exports/audit_log.csv)
1. Import 01_initial.json: 44 inserted.
2. Import again: 44 duplicates skipped, 0 new.
3. Import 02_update.json: 1 updated (M07 gets its transcript, 0 -> 27 turns, route changes from No usable text to Existing customer - support & setup).
4. Import again: 1 duplicate skipped.

## Decisions and limits
- Rule-based routing chosen over an LLM: reproducible and every flag shows its evidence. Keyword rules can miss nuance, so ambiguous cases go to review instead of guessing.
- Stored fields (is_connected, observed_outcome) are shown as source-reported, not truth.
- No company-ID join exists: M04 and M13 share an account label, shown only as a *proposed* association.
- SQLite file resets on Streamlit Cloud restart; re-import the bundled files.

## AI use
Code, routing rules and drafts were prepared with Claude, then tested on the provided data.
