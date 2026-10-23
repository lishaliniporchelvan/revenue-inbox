# Findings, rejected decision, slide outline, recording script

All counts are **selected records** (44: 30 calls, 14 meetings), 21 Aug 2026 IST. Not a team ranking.

## Finding 1: Stored outcome labels overstate interest
Of 6 calls labelled follow_up or referred with text, 3 are supported (C03, C07, C08) and 3 are not:
- C10 (follow_up): customer says "I said I will contact you if there is anything to discuss" and refuses a Monday call.
- C09 (referred): customer says they built software internally and "aren't looking for anything outside".
- C05 (follow_up): customer already has vendor and payment tools; only "if we ever decide to change".
Impact: pipeline built on stored labels would count 3 non-opportunities. Evidence: exports/needs_attention.csv, evidence column.

## Finding 2: Only 40.9% of records have usable text
18 of 44 usable (17 before the update batch). 20 background calls have null text (15 voicemail, 5 unresponsive). 3 meetings recorded but silent (M05 Pinehaven setup, M08 Amberleaf demo, M11 internal design). M12 and M14 notetaker cancelled, M13 never scheduled.
Also: source-reported connect rate is 11/30 (36.7%) but B11 and B14 are "connected" with 1 speaker and not a conversation; only 9 calls are flagged is_conversation. M05 and M08 look like real customer meetings by title but nothing was said, so they are in the review queue, not counted as outcomes.

## Finding 3: Existing-customer risk sits in meetings, not calls
- M04 Cedarwave: old card programme closing, cards stop working, identity checks again; user-to-department issue unresolved (00:31:48 to 00:32:45). M13 (migration discussion) has no capture; link to M04 is proposed only.
- M07 Willowfield (arrives in update batch): three product defects shown on screen; rep says no explanation yet (00:18:19).
- M09: expired invitation token for Brookmint Holdings, update promised same day or Monday (00:19:23).
These route to customer success and engineering follow-up, not sales.

## Decision considered and rejected
Merging records with similar titles (M04 "Cedarwave Distribution / SpendNest discussion" and M13 "SpendNest <> Cedarwave Distribution - Migration discussion") into one account activity. Rejected: data dictionary says similar titles are not a safe deduplication key and no company ID exists. Kept separate by (source_type, source_id), shown as proposed association with its evidence.

## 5 slides
1. Problem and scope: 44 selected records, what a sales manager needs to decide.
2. How the product works: import, route, flag, review, export (screenshot of Dashboard).
3. Findings 1 and 2 with evidence table.
4. Finding 3 plus the rejected decision.
5. Duplicate/update test results and limitations.

## 3-minute recording script
- 0:00 Dashboard: coverage 40.9%, connect rate with denominator, needs attention 18.
- 0:40 Useful workflow: filter Needs attention, open M04, show evidence quote and proposed link to M13.
- 1:20 Difficult routing: open C01 (partnership pitch AND cold call AND consent complaint). Explain primary Partner route, secondary Prospect outreach, flag shown. Then correct a route manually with a reason.
- 2:00 Duplicate/update test: Import tab, re-import 01 (44 skipped), import 02 (M07 updated), re-import 02 (skipped); show Audit log.
- 2:40 Export: generate, show manifest.json.
