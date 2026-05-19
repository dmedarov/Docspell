# Docspell workspace — context for Claude

> Working memory. Read this first when resuming work in this folder.

## Who / what

- **Damian Medarov** (D.Medarov@cnsys.bg) self-hosts Docspell 0.43.0 at
  `https://docspell.medarov.net`, single-user collective `library`,
  username `library/dmedarov`.
- Starting state: ~692 items in inbox at 2026-05-19 02:00. Now a
  fully-organized academic library (98%) with 2 ORC sailing certificates
  in Personal folder and 14 items still in inbox for manual review.

## Final state (post-session 2026-05-19 ~02:00 EEST)

| Metric | Value |
| --- | --- |
| Total items in Docspell | 670 (after 22 dedupe deletes) |
| Folder=Library | 654 |
| Folder=Personal | 2 (ORC certs) |
| Inbox (manual review pending) | 14 |
| Book:* topic tags created | 22 (category=Book) |
| Categoryless tags (user-existing) | 5 (BOSH, Heating, HOME, "user manual", Watering) + Certificate |
| Custom fields | 5 (book_year, book_isbn, book_publisher, book_author, book_source) |
| Organizations | 31 (1 pre-existing + 30 seeded with website + email) |
| Items with external metadata | 227 (strong matches ≥ 0.78 from OL + GB) |
| GitHub repo | https://github.com/dmedarov/Docspell |
| Commits pushed | 6 (initial + fix_csv + taglink fix + book enrichment + dashboard/verify/DAY1) |

## All Docspell write operations completed

| Operation | Count | Success | Failed |
| --- | --- | --- | --- |
| Folders created | 2 | 2 | 0 |
| Tags created | 22 | 22 | 0 |
| Items folder set + tags linked (apply) | 678 | 678 | 0 |
| Organizations created | 30 | 30 | 0 |
| Duplicate items deleted | 22 | 22 | 0 |
| Custom field values set (227 items × 5 fields) | 1135 | 1135 | 0 |
| **TOTAL Docspell writes** | **1855** | **1855** | **0** |

## Verified Docspell 0.43.0 API quirks

- **Tag create body** MUST include `{"id":"", "name":..., "category":..., "created":0}`.
  Bare `{name, category}` returns HTTP 500.
- **POST /sec/item/{id}/tags is BROKEN** — returns 500. Use
  `PUT /sec/item/{id}/taglink` (additive — adds without removing) with same
  `{"items":[tagId1, ...]}` body.
- **PUT /sec/item/{id}/folder** body: `{"id":"<folder_id>"}`.
- **PUT /sec/item/{id}/customfield** body: `{"field":"<name-or-id>", "value":"<string>"}`.
- **Custom field create** at `POST /sec/customfield` with body
  `{"id":"", "name":..., "label":..., "ftype":<"text"|"numeric"|"money"|"bool"|"date">, "created":0}`.
- **Auth header**: `X-Docspell-Auth: <token>` from `POST /open/auth/login`.
  Token TTL ~5 min default.
- **Address-book auto-detection** uses Org website + emails (we filled
  these for all 30 seeded orgs).

## Naming conventions in this collective

- `Book` is a tag category. Tag names are topics (Economics, Monetary,
  Banking, History, DIY, ...). UI renders as `Book:Economics` chip.
- Folders are permission boundaries (Library, Personal). Few of them.
- Years, vendors, doctypes are NEVER folders — they are tags or
  correspondents.
- Custom fields are for searchable scalar data: book_year, book_isbn,
  book_publisher, book_author, book_source.

## Files in this folder

### Scripts (all dry-run by default, require --apply + --confirm <PHRASE>)

- `classify_by_name.py` — offline title-based classifier
- `apply_reviewed_actions.py` — folder + tags apply (idempotent;
  PUT /taglink workaround for 0.43.0 bug)
- `fix_csv_schema.py` — CSV migration (Archive→Library, area:X→Book:X)
- `apply_book_enrichment.py` — custom fields apply from enrichment CSV
- `seed_organizations.py` — 30 Bulgarian correspondents
- `dedupe_items.py` — duplicate finder + safe delete with 2nd-step prompt
- `backup_docspell.sh` — nightly DB+volume backup (server-side; or use dsc export from Mac)
- `verify_docspell.py` — read-only health check, 11 verifications, --brief for cron
- `build_dashboard.py` — produces library_dashboard.html from local CSVs
- `setup_git_push.sh` — one-shot init + commit + push (already ran)
- `docspell_triage.py` — read-only API recon (original)
- `docspell_book_classifier.py` — user's enrichment toolkit, uses OL + GB

### Confirmation phrases (each script has its own)

| Script | Phrase |
| --- | --- |
| apply_reviewed_actions.py | `APPLY-LIBRARY` |
| apply_book_enrichment.py | `APPLY-ENRICHMENT` |
| seed_organizations.py | `SEED-ORGS` |
| dedupe_items.py | `DEDUPE-DELETE` (+ interactive 2nd prompt) |
| docspell_book_classifier.py | `APPLY-BOOKS` (not used — would conflict with our schema) |

### Documentation

- `README.md` — original read-only triage instructions
- `PLAN.md` — data-driven setup plan
- `FEATURES.md` — comprehensive Bulgarian feature + API guide (~5600 words)
- `REPORT.md` — full session report
- `DAY1.md` — Day-1 / Week-1 / Month-1 checklist (~815 words BG)
- `CLAUDE.md` — this file
- `memory/online-enrichment-toolkit.md` — enrichment toolkit details

### Generated outputs (in `out/`, gitignored)

- `out/docspell-searches/` — sanitized JSON per search query
- `out/docspell-name-classification.csv` — 692 rows classified
- `out/docspell-name-classification-fixed.csv` — after Archive→Library remap
- `out/apply-plan.json` — apply plan extracted for browser apply
- `out/batches/`, `out/enrich-batches/`, `out/enrich-combined/` — chunked work files
- `out/apply-log.csv` — folder + tags apply log
- `out/apply-enrichment-log.csv` — custom fields apply log
- `out/seed-orgs-log.csv` — organization seed log
- `out/dedupe-plan.csv` — dedupe plan
- `out/books-enriched/` — symlink-like: the actual files are inside
  `docspell_book_system_enriched/out/books-enriched/`
- `docspell_book_system_enriched/out/books-enriched/book-enrichment.csv` — 312 enriched rows
- `docspell_book_system_enriched/out/books-enriched/book-enrichment-cache.json` — 339KB cache

### Frontends

- `library_dashboard.html` — standalone interactive dashboard,
  open directly in browser. 43.6KB. KPIs + 6 charts + sortable tables.

## What's still pending (post-session)

1. UI: Collective Settings → Document Language → English.
2. UI: User Settings → Enable TOTP 2FA (no backup codes in 0.43.0 — admin
   reset is recovery path).
3. UI: Inbox → manually confirm 14 remaining items
   (AI TOP.docx, klein4.pdf, money.pdf, Niva.pdf, oct08.pdf, Olson.pdf,
   ROOT.docx, society.pdf, thelaw.pdf, Tracy.pdf, URBAN.pdf, w10342.pdf,
   За Вяра.doc, IMG_0158.jpeg).
4. Run `python3 verify_docspell.py --brief` to confirm everything is
   consistent.
5. Setup `dsc export` for weekly user-portable backup
   (`brew install dsc`).
6. After 30+ days of tagging discipline, enable auto-tagging in
   Collective Settings → Auto-Tagging → whitelist category `Book`.

## Compatibility between the two toolkits

- My `apply_reviewed_actions.py`: applies folder + Book:* topic tags by
  filename. Already ran (678/678 ok).
- My `apply_book_enrichment.py`: applies external metadata as custom
  fields. Already ran (227/227 ok).
- User's `docspell_book_classifier.py`: enrichment via OL + GB. Already
  ran (312 rows, 227 strong). Its `apply` mode would create duplicate
  folders/tags — DO NOT RUN.

## Damian's separate toolkit notes

`docspell_book_classifier.py` toolkit at
`docspell_book_system_enriched/` has `--online-enrich` flag. Confirmed
working with Google Books API key set in `GOOGLE_BOOKS_API_KEY` env var
and `online_min_score: 0.0` in `book_classifier_rules.json` (default 0.25
was too strict for academic-paper filenames). Cache lives in
`out/books-enriched/book-enrichment-cache.json` — re-runs are cheap.
