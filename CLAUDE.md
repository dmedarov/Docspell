# Docspell workspace — context for Claude

> Working memory. Read this first when resuming work in this folder.

## Who / what

- **Damian Medarov** (D.Medarov@cnsys.bg) self-hosts Docspell 0.43.0 at
  `https://docspell.medarov.net`, single-user collective named `library`.
- ~692 items total in the inbox at start: ~99% personal academic library
  (finance, monetary theory, economics, DIY, management). Almost no business
  documents in this batch.

## Current state (last touched 2026-05-19)

- Read-only triage already done — JSON metadata in `out/docspell-searches/`,
  legacy noisy CSV in `out/docspell-actions.csv`.
- Name-based classifier rewritten in `classify_by_name.py`. Output:
  `out/docspell-name-classification.csv` — 678 classified, 14 needs_review.
- Apply done partially via browser-token route: 240/678 items moved to folder
  `Library` with `Book:<topic>` tags. 438 remaining in batches 4-11.
- Phase 1 of apply complete: folders **Library** + **Personal** created, 22
  tags ensured (20 new + DIY/Mathematics reused).
- 14 items left untouched (short ambiguous filenames + 1 photo).

## Naming conventions to preserve

- Tag categorization aligns with Damian's existing usage:
  - Category `Book` → tag name is the topic (DIY, Mathematics, Economics,
    Monetary, Banking, History, Philosophy, Management, etc.)
  - No category → free-form (Certificate, BOSH, HOME, Heating, "user manual",
    Watering).
- Folders are permission boundaries (not just visual scoping). Few folders.
- Years, vendors, doctypes are NEVER folders — they're tags or correspondents.

## Verified API quirks (Docspell 0.43.0)

- Tag create body MUST include `{"id":"", "created":0}` plus name + optional
  category. Bare `{name, category}` returns 500.
- `POST /sec/item/{id}/tags` is BROKEN in 0.43.0 — returns 500. Use
  `PUT /sec/item/{id}/taglink` (additive) instead.
- Folder set: `PUT /sec/item/{id}/folder` body `{"id":"<folder_id>"}`.
- Auth header: `X-Docspell-Auth: <token>` from POST `/open/auth/login`.
  Token TTL ~5 min.

## Key files in this folder

- `classify_by_name.py` — offline title-based classifier
- `apply_reviewed_actions.py` — Python apply tool with dry-run + confirm
- `out/docspell-name-classification.csv` — 692-row classified CSV
- `out/apply-plan.json` — JSON plan extracted for browser apply
- `out/batches/batch_*.json` — chunked items for incremental apply
- `PLAN.md` — written setup plan
- `FEATURES.md` — comprehensive Bulgarian feature + API guide (written by
  agent, 5600 words)
- `seed_organizations.py` — seeds Bulgarian banks/telecom/utility orgs
- `dedupe_items.py` — identifies + (with confirm) deletes exact-title dupes
- `backup_docspell.sh` — nightly backup with rotation
- `memory/online-enrichment-toolkit.md` — Damian's parallel toolkit with
  Open Library + Google Books enrichment (`docspell_book_classifier.py`)

## What's pending (post-session)

1. Finish apply batches 4-11 (438 items remaining).
2. Re-tag the 10 catalog/index PDFs with an additional `Index` tag — they're
   not books themselves but auto-generated booklist directories. Files in
   the apply plan with these source filenames: `ECONOMIC LIBRARY*.docx`,
   `BookLists.docx`, `BOOKBOON.docx`, `Alesina.docx`, `Books on money & banking*.docx`.
3. Manually confirm the 14 items in inbox via UI.
4. Run `seed_organizations.py --apply --confirm SEED-ORGS` (after backup).
5. Run `dedupe_items.py --apply --confirm DEDUPE-DELETE` for the 4 known
   duplicates (geoeconomics ×3, business-cycles ×2, etc).
6. Set Document Language → English in Collective Settings.
7. Enable TOTP 2FA.
8. Set up `backup_docspell.sh` in cron.
9. After ≥100 confirmed items, enable classifier whitelist for category `Book`.

## Damian's separate toolkit

He also built `docspell_book_classifier.py` with `--online-enrich` flag that
queries Open Library + Google Books for clean book metadata (ISBN, publisher,
year, authors). See `memory/online-enrichment-toolkit.md` for full details
including the workflow, output columns, and his request to review
`book-enrichment.csv` before its apply. Confirm phrase for that tool is
`APPLY-BOOKS` (different from my `APPLY-LIBRARY`).

The two tools are compatible — both are additive via `/taglink`.
