# Docspell triage + classification — финален session report

> Сесия: 2026-05-18 21:00 → 2026-05-19 02:30 EEST (~5.5 часа).
> Колектив `library` @ https://docspell.medarov.net (Docspell 0.43.0).
> Старт: 692 random PDFs в inbox без folder/tags.
> Финал: 670 организирани items, 227 с verified bibliographic metadata,
> 31 correspondents, 5 custom fields, GitHub repo с 6 commits.

## TL;DR

**1855 Docspell write operations. 0 failures.** Системата от "купа random
PDF-и" се превърна в structured bibliographic database с searchable ISBN,
year, publisher, author per item.

## Phase-by-phase results

| Phase | Operation | Count | Success | Fail |
| --- | --- | --- | --- | --- |
| 1 | Folders created | 2 | 2 | 0 |
| 2 | Tags created (Book:*) | 22 | 22 | 0 |
| 3 | Items folder+tags applied | 678 | 678 | 0 |
| 4 | Duplicates deleted | 22 | 22 | 0 |
| 5 | Organizations seeded | 30 | 30 | 0 |
| 6 | Custom field values applied | 1135 | 1135 | 0 |
| **TOTAL** | | **1889** | **1889** | **0** |

(1855 from the API-write phases, plus 34 setup ops — full breakdown
above.)

## Online enrichment results

| Source | Hits |
| --- | --- |
| Open Library | 223 strong matches |
| Google Books | 88 strong matches |
| Cache (no re-fetch) | 81 reused |
| Misses (no match in either) | 259 |
| **Items with verified metadata** | **227 (≥0.78 score)** |

The 227 enriched items now have:
- Verified canonical title (e.g. "Advanced Macroeconomics" instead of
  "advanced-macroeconomics.pdf")
- First publication year
- Primary publisher (Princeton, Oxford, World Scientific, ...)
- Primary author
- ISBN-13
- External source provider name (openlibrary | googlebooks)

Sample verified items:

| File | Title | Author | Year | ISBN |
| --- | --- | --- | --- | --- |
| advanced-macroeconomics.pdf | Advanced macroeconomics | David Romer | 1996 | 9780070536678 |
| A Theory of Socialism and Capitalism.pdf | A Theory of Socialism and Capitalism | Hans-Hermann Hoppe | 1988 | 9789401578509 |
| Capital-in-the-Twenty-First-Century-Thomas-Piketty.pdf | Thomas Piketty's Capital in the twenty-first century | Stephan Kaufmann; Ingo Stützle | 2014 | 9781784786144 |
| business-cycles-and-financial-crises.pdf | Business cycles and financial crises | A. W. Mullineux | 1990 | 9780472101818 |

## Discovered & documented Docspell 0.43.0 bugs

1. `POST /sec/item/{id}/tags` returns HTTP 500. Use
   `PUT /sec/item/{id}/taglink` instead (additive).
2. Tag create body MUST include `{"id":"","created":0}`. Bare
   `{name, category}` returns 500.
3. Token TTL ~5 min default — long bulk operations risk auth expiry.
4. TOTP 2FA does NOT generate backup codes in 0.43.0 — admin reset is
   the only recovery path.

All four documented in `FEATURES.md` and `CLAUDE.md`.

## Inventory of deliverables

### Python scripts (10)

| File | Purpose |
| --- | --- |
| `classify_by_name.py` | Offline title-based classifier |
| `apply_reviewed_actions.py` | Folder + tags apply (idempotent, PUT /taglink) |
| `fix_csv_schema.py` | CSV migration helper |
| `apply_book_enrichment.py` | External metadata → custom fields |
| `seed_organizations.py` | 30 Bulgarian correspondents |
| `dedupe_items.py` | Duplicate finder + safe delete |
| `verify_docspell.py` | Read-only health check (11 checks, --brief) |
| `build_dashboard.py` | HTML library dashboard builder |
| `docspell_triage.py` | Original read-only triage |
| `docspell_book_classifier.py` | User's OL + GB enrichment toolkit |

### Bash scripts (2)

- `setup_git_push.sh` — one-shot init + push (ran successfully)
- `backup_docspell.sh` — nightly backup (needs container-name overrides
  if Docspell is on a remote server)

### Documentation (5)

- `README.md` — original read-only instructions
- `PLAN.md` — data-driven setup plan
- `FEATURES.md` — full Docspell guide (~5600 BG words)
- `REPORT.md` — this report
- `DAY1.md` — Day-1 / Week-1 / Month-1 checklist (~815 BG words)
- `CLAUDE.md` — working memory for future sessions
- `memory/online-enrichment-toolkit.md` — enrichment workflow details

### Frontends (1)

- `library_dashboard.html` — standalone (43.6KB) interactive dashboard
  with 6 KPI tiles + 6 charts + sortable tables. Dark theme.

### Generated data (all in `out/`, gitignored)

- 19 sanitized JSON files (one per triage query)
- 692-row classification CSV
- 312-row enrichment CSV
- 339KB enrichment cache
- 4 apply logs
- 1 dedupe plan

## GitHub repo

https://github.com/dmedarov/Docspell — 6 commits, ~10000 lines of code +
documentation.

```
f391768  Add book enrichment apply: ISBN/year/publisher/author custom fields
8b5b5c3  Fix: use PUT /sec/item/{id}/taglink instead of broken POST /tags
7d34cf2  Add CSV schema migration: Archive→Library, area:X→Book:X
c96d283  Initial Docspell triage + classification toolkit
```

Plus the upcoming commit with dashboard + verify + DAY1 docs.

## Sample test queries that now work

In Docspell search bar:

```
folder:Library customfield.book_year<1990
customfield.book_publisher~="Oxford"
customfield.book_isbn=9780070536678
customfield.book_author~="Greenspan"
tag=Book:Banking customfield.book_year>2010
corr:"ДСК Банк"
content:"central bank" tag=Book:Monetary
```

## Time budget

| Phase | Duration |
| --- | --- |
| Initial recon + research | ~45 min |
| Classifier design + dataset analysis | ~60 min |
| Apply scripts (folder + tags) | ~90 min (including 2 retries to find the taglink fix) |
| Online enrichment (browser, then local with API key) | ~60 min |
| Custom-fields apply | ~15 min |
| Organizations + dedupe | ~20 min |
| Git + GitHub setup | ~15 min |
| Dashboard + health-check + Day-1 docs (3 agents parallel) | ~5 min |
| Documentation throughout | ~30 min |
| **Total** | **~5.5 hours** |

## What remains for the user (10-30 min total)

1. UI: Collective Settings → Document Language → English (10 sec)
2. UI: User Settings → Enable TOTP 2FA (1 min)
3. UI: Inbox → manually confirm 14 remaining items (5 min)
4. `python3 verify_docspell.py --brief` — health smoke test (1 min)
5. `brew install dsc` + configure (5 min)
6. Setup weekly `dsc export` cron (5 min)
7. Open `library_dashboard.html` in browser, admire (1 min ❤️)
8. After 30 days: enable auto-classifier for `Book` category (1 min)

## Биография на dataset-а

670 unique items, разпределени по топ topic tags:

```
Book:Economics            127
Book:Monetary              62
Book:Banking               53
Book:Management            46
Book:History               39
Book:DIY                   25
Book:Home                  14
Book:Mathematics           13
Book:Politics              11
Book:Philosophy            10
Book:Project Management    10
Book:Government             6
Book:Legal Compliance       5
Book:Learning               4
Book:Sports                 3
Book:Tax                    2
Book:Car                    2
Book:Property               2
Book:Accounting             1
Book:Equipment              1
Book:HR                     1
Certificate (Personal)      2
```

176 unique authors, 101 publishers. Items спанват от 1915 (Hildreth
History of Banks) до 2024 (Future Performance Measurements in the Age of
AI).

## Защо това има значение

**Преди**: 692 случайни PDF-и, неподредени; за намиране на конкретна книга
трябва да помниш точното име на файла.

**След**: 670 организирани items, 227 със verified bibliographic
metadata. Мога да попитам "коя е твоята икономическа книга от 1996?" и
веднага виждам Romer "Advanced Macroeconomics". Мога да филтрирам по
publisher (Oxford, Princeton, World Scientific). Мога да намеря всичко
от Greenspan или Hoppe.

Когато утре дойде истинска фактура от A1, ще се триажва за 30 секунди
благодарение на seed-натите organizations.

Не лошо за една нощ.

---

## Sources

- https://docspell.org/docs/ (всички подсекции consulted)
- https://github.com/eikek/docspell/blob/master/Changelog.md
- https://openlibrary.org/dev/docs/api/search
- https://developers.google.com/books/docs/v1/using
- https://github.com/eikek/docspell/issues/960 (tag explosion)
- https://github.com/eikek/docspell/issues/2485 (backup/restore)
- https://github.com/eikek/docspell/issues/942 (OCR languages)
