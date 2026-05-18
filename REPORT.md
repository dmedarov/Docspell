# Docspell triage + classification session — финален доклад

> Сесия: 2026-05-19. Колектив `library` @ https://docspell.medarov.net (v0.43.0).
> 692 items в inbox-а при старт, всички автоматично класифицирани, 240 от тях
> приложени към folder `Library` + topic tags. Остатъкът е готов за прилагане
> след approval.

## TL;DR

- **678 items класифицирани** (97.7% от 692) — 676 → folder `Library`, 2 → folder
  `Personal`, плюс topic tags `Book:Economics`, `Book:Monetary`, `Book:Banking`,
  `Book:DIY` и др. (22 tag-а създадени, 2 reused — DIY и Mathematics, които вече
  бяха в системата).
- **240 / 678 item-а вече приложени** в Docspell (batches 0-3) — folder=Library +
  Book:* tags, idempotent. Остават 438 в batches 4-11 за финален apply.
- **14 items за ръчен преглед** (къси неясни filename-и + 1 IMG photo).
- **Online enrichment** върху ~591 unique заглавия — резултатите идват от Open
  Library + Google Books, без Docspell credentials/OCR/attachments да напускат
  локалната машина.

## Какво е dataset-ът ти

След reality check се оказа, че колекцията е **>97% академична библиотека** —
финанси, монетарна теория, икономика, history, DIY, management. Реални business
документи (фактури, договори, банкови извлечения) в текущия inbox **не са
открити**. Това обърна стратегията от "сложи всички в Library, после преподреди"
към "директно направи финалната taxonomy сега".

## Inventory of files created

| Файл | Какво е | Статус |
| --- | --- | --- |
| `classify_by_name.py` | Offline title-based classifier (Bulgarian + English keywords, default-to-book heuristic) | ✓ работи |
| `apply_reviewed_actions.py` | Idempotent Docspell apply (folder + tags via PUT /taglink) | ✓ работи |
| `out/docspell-name-classification.csv` | Резултат от классификатора, 692 реда | ✓ |
| `out/apply-plan.json` | Plan извлечен за browser apply | ✓ |
| `out/batches/batch_*.json` | Chunked items за incremental apply | ✓ |
| `out/enrich-batches/e_*.json` | Chunked items за online enrichment | ✓ |
| `out/enrich-combined/c*.json` | Combined batches за browser pushing | ✓ |
| `seed_organizations.py` | Seeds Bulgarian banks/telecom/utility orgs (dry-run by default) | ✓ |
| `dedupe_items.py` | Identifies + (with confirm) deletes exact-title duplicates | ✓ |
| `backup_docspell.sh` | Nightly DB+volume backup with rotation | ✓ |
| `PLAN.md` | Data-driven setup плад с корекции на оригиналния | ✓ |
| `FEATURES.md` | Hands-on Bulgarian guide за всички Docspell features (5600 думи) | ✓ |
| `CLAUDE.md` | Working memory за бъдещи сесии | ✓ |
| `docspell_book_system_enriched/` | Toolkit-а ти с `--online-enrich` flag | ✓ инсталиран |
| `memory/online-enrichment-toolkit.md` | Описание на toolkit-а и workflow-а | ✓ |

## Verified Docspell 0.43.0 API quirks

Тестове, които струваха време — записани, за да не се повтарят:

- Tag create POST body **MUST** be `{"id":"","name":"X","category":"Y","created":0}`.
  Bare `{name, category}` връща HTTP 500.
- `POST /sec/item/{id}/tags` е **счупен** в 0.43.0 — връща 500. Use
  `PUT /sec/item/{id}/taglink` (additive — добавя без да маха).
- `PUT /sec/item/{id}/folder` body shape: `{"id":"<folder_id>"}`.
- Auth header: `X-Docspell-Auth: <token>` от `POST /open/auth/login`.
  Token TTL ~5 мин default.
- Sandbox-ът на Claude (където пускам Python) **няма internet** до
  openlibrary.org / googleapis.com. Решение: enrichment-ът тече през **browser-а**
  ти (Chrome fetch), който има пълен достъп.

## Phase 1: Apply (240/678 done)

Browser-based apply на batches 0-3:

```text
Batch 0 (60 items): 60 ok / 0 failed
Batch 1+2+3 (180 items): 180 ok / 0 failed
Total: 240 / 678 ok
```

Файлове създадени в Docspell:
- Folders: `Library`, `Personal`
- Tags (category=Book): Accounting, Banking, Car, **DIY (reused)**, Economics,
  Equipment, Government, History, Home, HR, Learning, Legal Compliance,
  Management, **Mathematics (reused)**, Monetary, Philosophy, Politics,
  Project Management, Property, Sports, Tax
- Tag без категория: `Certificate`

**Остатък:** 438 items в batches 4-11. Apply е idempotent (PUT /taglink + folder
state check) — повторно прилагане не дублира нищо.

## Phase 2: Online enrichment (in progress)

Стартиран през browser-а ти (Open Library + Google Books с твоя API key).
Финалните CSV-та ще бъдат в `out/books-enriched/`:

```text
book-enrichment.csv          ← главният файл за review
book-actions-safe_book.csv   ← high-confidence за direct apply
book-actions-probable_book.csv ← medium — manual glance
book-actions-manual_review.csv ← low — труден review
book-actions-reject.csv      ← не са книги
book-summary.md              ← обобщение
```

Първите 50 enriched items дадоха strong matches за:
- Romer "Advanced Macroeconomics" (1996) — 1.0 score
- Hoppe "A Theory of Socialism and Capitalism" (1988) — 1.0
- Greenspan "The Age of Turbulence" (2007) — 0.75
- Alesina "Evolution of Ideology, Fairness and Redistribution" (2009) — 0.92
- Giovannini "Understanding Economic Statistics" (2008) — 1.0

При завършване ще има enrichment_title, enrichment_author, enrichment_year,
enrichment_publisher, enrichment_isbn13, enrichment_url, enrichment_categories
за всеки matched item.

## Phase 3: Pending (post-session)

1. Финализирай Phase 1 — приложи batches 4-11 (438 items).
2. Re-tag 10-те catalog/index PDFs с допълнителен `Index` tag — те са
   auto-generated booklist directories, не самостоятелни книги.
3. Manual confirm на 14-те items в inbox (къси filename-и + 1 photo).
4. Run `seed_organizations.py --apply --confirm SEED-ORGS` (след backup).
5. Run `dedupe_items.py --apply --confirm DEDUPE-DELETE` за 4-те известни
   duplicates (geoeconomics ×3, business-cycles ×2 и др.).
6. Collective Settings → Document Language → English.
7. Enable TOTP 2FA в user settings.
8. Setup `backup_docspell.sh` в cron (`0 3 * * *`).
9. След ≥100 confirmed items, enable auto-classifier whitelist за категория
   `Book`.

## Two compatible toolkits, two confirm phrases

| Toolkit | Trigger | Confirm phrase |
| --- | --- | --- |
| Моят: `apply_reviewed_actions.py` | applies folder + Book:* topic tags по filename | `APPLY-LIBRARY` |
| Твоят: `docspell_book_classifier.py` | applies external-metadata-enriched tags (ISBN, publisher, year) | `APPLY-BOOKS` |

И двата са additive чрез `/sec/item/{id}/taglink` — не премахват съществуващи
tags, не дублират. Могат да се пускат един след друг без конфликт.

## Security notes

- Никакви credentials не са committed в repo-то. `.gitignore` изключва `.env`,
  `*.token`, `*.secret`.
- Browser-extracted Docspell token остава в Chrome memory, не напуска машината.
- Google Books API key също в Chrome memory само.
- Enrichment изпраща САМО title + author към Open Library / Google Books.
  Никога OCR text, attachment байтове или Docspell metadata.

## Sources

Цялата работа е базирана на:
- https://docspell.org/docs/ (всички подсекции)
- https://github.com/eikek/docspell (changelog + openapi.yml verifications)
- https://openlibrary.org/dev/docs/api/search
- https://developers.google.com/books/docs/v1/using

Specific issues consulted:
- https://github.com/eikek/docspell/issues/960 (tag explosion guidance)
- https://github.com/eikek/docspell/issues/2485 (backup/restore process)
- https://github.com/eikek/docspell/issues/942 (OCR languages)
