# Docspell taxonomy schema — `library` collective

> Authoritative reference for the folders, tag categories, custom fields,
> and organizations that have been applied to the `library` collective at
> https://docspell.medarov.net (Docspell 0.43.0).
>
> Last verified: 2026-05-19 02:30 EEST.
> Apply scripts use this schema as ground truth.

## Folders

Folders in Docspell are permission boundaries (each has an owner +
optional members). Items can be in at most one folder. We keep the
folder count small.

| Folder name | Item count | Role |
| --- | ---: | --- |
| `Library` | 654 | All books, manuals, reference materials |
| `Personal` | 2 | Personal documents (ORC sailing certificates) |
| (none / inbox) | 14 | Pending manual review |

Folders to NOT use (anti-patterns): years (2024, 2025), vendor names
(EVN, A1), document types (Invoices, Contracts). Those belong in tags
or correspondents.

## Tag categories

Docspell supports a native `category` field on tags. The UI renders
`category:name` chips. We use four categories:

### Category `Book` — topic tags for library items

22 tags, each item gets 0-3 of these. Pre-existing tags `DIY` and
`Mathematics` were preserved; the other 20 were created.

| Tag name | Item count |
| --- | ---: |
| `Book:Economics` | 127 |
| `Book:Monetary` | 62 |
| `Book:Banking` | 53 |
| `Book:Management` | 46 |
| `Book:History` | 39 |
| `Book:DIY` | 25 |
| `Book:Home` | 14 |
| `Book:Mathematics` | 13 |
| `Book:Politics` | 11 |
| `Book:Philosophy` | 10 |
| `Book:Project Management` | 10 |
| `Book:Government` | 6 |
| `Book:Legal Compliance` | 5 |
| `Book:Learning` | 4 |
| `Book:Sports` | 3 |
| `Book:Tax` | 2 |
| `Book:Car` | 2 |
| `Book:Property` | 2 |
| `Book:Accounting` | 1 |
| `Book:Equipment` | 1 |
| `Book:HR` | 1 |

### Category `Smart home` — pre-existing user category

| Tag name | Item count |
| --- | ---: |
| `Smart home:Watering` | (user-managed) |

### No category — pre-existing user tags + Certificate

Free-form tags without category. Preserved as user had them.

| Tag name | Notes |
| --- | --- |
| `BOSH` | Brand reference for kitchen boiler manual |
| `Heating` | HVAC documents |
| `HOME` | Generic home category |
| `user manual` | Product manuals |
| `Certificate` | ORC sailing certificates (added by apply script) |

## Custom fields

5 custom fields created by `apply_book_enrichment.py`, populated with
verified bibliographic metadata from Open Library + Google Books.

| Name | Type | Filled count | Example value |
| --- | --- | ---: | --- |
| `book_year` | numeric | 227 | `1996` |
| `book_isbn` | text | 195 | `9780070536678` |
| `book_publisher` | text | 215 | `World Scientific` |
| `book_author` | text | 227 | `David Romer` |
| `book_source` | text | 227 | `openlibrary` or `googlebooks` |

Query examples:

```text
customfield.book_year>=1990 customfield.book_year<2000
customfield.book_publisher~="Oxford"
customfield.book_publisher~="Princeton"
customfield.book_isbn=9780070536678
customfield.book_author~="Greenspan"
f.book_year=2007                       # shorthand for customfield.*
```

## Organizations (correspondents)

31 organizations seeded via `seed_organizations.py` plus the 1
pre-existing. Each has `website` + `email` contacts so Docspell's
address-book auto-detection fires on incoming documents.

### Banking (8)

| Name | Website | Email |
| --- | --- | --- |
| ДСК Банк | dskbank.bg | info@dskbank.bg |
| UniCredit Bulbank | unicreditbulbank.bg | info@unicreditgroup.bg |
| Postbank | postbank.bg | support@postbank.bg |
| Fibank | fibank.bg | office@fibank.bg |
| ОББ (UBB) | ubb.bg | ubb@ubb.bg |
| ProCredit Bank | procreditbank.bg | info@procreditbank.bg |
| Allianz Bank | allianz.bg | info@allianz.bg |
| Райфайзенбанк | rbb.bg | info@rbb.bg |

### Telecom (3)

| Name | Website | Email |
| --- | --- | --- |
| A1 България | a1.bg | customer@a1.bg |
| Vivacom | vivacom.bg | info@vivacom.bg |
| Yettel | yettel.bg | info@yettel.bg |

### Utility (7)

| Name | Website | Email |
| --- | --- | --- |
| EVN | evn.bg | info@evn.bg |
| ЧЕЗ | cez.bg | info@cez.bg |
| Energo-Pro | energo-pro.bg | info@energo-pro.bg |
| Топлофикация София | toplo.bg | office@toplo.bg |
| Софийска вода | sofiyskavoda.bg | customers@sofiyskavoda.bg |
| Овергаз | overgas.bg | office@overgas.bg |
| Булгаргаз | bulgargaz.bg | office@bulgargaz.bg |

### Government (2)

| Name | Website | Email |
| --- | --- | --- |
| НАП | nra.bg | infocenter@nra.bg |
| НОИ | nssi.bg | noi@nssi.bg |

### Logistics (4)

| Name | Website | Email |
| --- | --- | --- |
| Speedy | speedy.bg | info@speedy.bg |
| Econt | econt.com | info@econt.com |
| DHL | dhl.bg | info.bg@dhl.com |
| DPD | dpd.com | info@dpd.bg |

### IT (6)

| Name | Website | Email |
| --- | --- | --- |
| JetBrains | jetbrains.com | sales@jetbrains.com |
| GitHub | github.com | contact@github.com |
| Atlassian | atlassian.com | sales@atlassian.com |
| Google | google.com | support@google.com |
| Microsoft | microsoft.com | support@microsoft.com |
| AWS | aws.amazon.com | aws-billing@amazon.com |

## Future tags to add (not yet present)

When real business documents start arriving, create these tag
categories. The classifier already detects them but we haven't applied
them since the collective currently has 0 business docs.

### Category `doctype` (for non-book documents)

| Tag name | Purpose |
| --- | --- |
| `doctype:invoice` | Invoices (fаktura, инвойс) |
| `doctype:receipt` | Receipts, касов бон |
| `doctype:contract` | Contracts, договори |
| `doctype:bank-statement` | Bank statements |
| `doctype:tax` | Tax documents, НАП forms |
| `doctype:insurance` | Insurance policies |
| `doctype:warranty` | Warranty certificates |
| `doctype:manual` | Product user manuals |
| `doctype:report` | Annual/quarterly reports |
| `doctype:cv` | CVs, autobiographies |
| `doctype:id-document` | ID cards, passports |
| `doctype:medical` | Medical records, prescriptions |

### Category `status` (workflow tracking — NOT for auto-tagging)

| Tag name | Purpose |
| --- | --- |
| `status:todo` | Needs action |
| `status:waiting` | Waiting on response |
| `status:paid` | Invoice paid |
| `status:archived` | Done, archive |

Docspell auto-classifier learns from text — `status` tags are NOT
content-derived, so they must be excluded from the auto-tagging
category whitelist.

### Custom fields to add when invoices arrive

| Name | Type | Purpose |
| --- | --- | --- |
| `invoice_number` | text | Invoice ID for reference |
| `amount` | money | Invoice total |
| `due_date` | date | Payment deadline |
| `paid_date` | date | When marked paid |
| `vat_period` | text | e.g. "2026-Q2" |

## Apply confirmation phrases

Each apply script has a unique confirmation phrase to prevent accidental
runs:

| Script | Phrase |
| --- | --- |
| `apply_reviewed_actions.py` | `APPLY-LIBRARY` |
| `apply_book_enrichment.py` | `APPLY-ENRICHMENT` |
| `seed_organizations.py` | `SEED-ORGS` |
| `dedupe_items.py` | `DEDUPE-DELETE` (plus interactive 2nd prompt) |
| `docspell_book_classifier.py` (user's toolkit, not used) | `APPLY-BOOKS` |

## Docspell 0.43.0 API quirks documented during this work

1. `POST /sec/item/{id}/tags` returns HTTP 500. Use
   `PUT /sec/item/{id}/taglink` body `{"items":[<tag-ids-or-names>]}`
   (additive — does not remove existing tags).
2. Tag create body **MUST** include `{"id":"","name":..,"category":..,"created":0}`.
   Bare `{name, category}` returns 500.
3. Custom field create at `POST /sec/customfield` body
   `{"id":"","name":..,"label":..,"ftype":"text|numeric|money|bool|date","created":0}`.
4. Item folder set: `PUT /sec/item/{id}/folder` body `{"id":"<folder_id>"}`.
5. Item custom field value: `PUT /sec/item/{id}/customfield` body
   `{"field":"<field-id-or-name>","value":"<string>"}`.
6. Token TTL ~5 min default → long bulk operations may need re-login
   mid-stream.
7. TOTP 2FA does NOT generate backup codes in 0.43.0 — admin reset is
   the only recovery path.
8. Tesseract base image ships `eng` + `deu` only; `bul` requires
   derived image or tessdata volume mount.
9. **Query language for custom fields — empirically mapped in 0.43.0:**
   - `customfield.book_year>0` → ParseFailure (unknown prefix)
   - `f.book_year>0` → ParseFailure (expects `:` separator)
   - `exist:f.book_year` → ParseFailure (`exist:` only works on
     relationship fields like `conc.equip.id`, `corr.org.name`)
   - **`f.book_year:>0`** → works (colon + operator + value)
   - **`f.book_publisher:Oxford`** → works (exact match)
   - **`f.book_publisher:~Oxford`** → works (case-insensitive substring)
   - Range: `f.book_year:[1990;2000]`

## Multi-language OCR (`bul+eng+rus`) — joex config override

Docspell v0.43.0 does **not** include Bulgarian in `Language.scala`'s
enum (supported: deu/eng/fra/ita/spa/hun/por/ces/khm/dan/fin/nor/swe/
rus/ron/nld/lav/jpn/heb/lit/pol/est/ukr/svk). However the joex
container's bundled Tesseract 5.5.0 ships with 161 languages
including `bul`, `rus`, and the `Cyrillic` group.

Workaround: mount a HOCON config file at
`/opt/docspell-joex/conf/docspell-joex.conf` (file is `conf/joex-override.conf`
in this repo, pushed to pve at `/root/docspell-docker/docker-compose/conf/`).
The file hardcodes `-l bul+eng+rus` in three places where Docspell
substitutes `{{lang}}`:

1. `extraction.ocr.tesseract.command.args` — text extraction
2. `convert.tesseract.command.args` — image→PDF
3. `convert.ocrmypdf.command.args` — PDF→PDF/A with text layer

**Critical gotcha discovered the hard way**: when `config.file`
system property points to an existing file, Docspell's
`ConfigFactory.default` switches from `EnvConfig` (custom env-var→
HOCON mapping with `_/__/___` underscore conventions) to Typesafe
Config's `ConfigSource.default`. The latter does NOT auto-apply
docker-compose env vars like `DOCSPELL_JOEX_JDBC_URL`. Result: joex
silently falls back to bundled reference.conf defaults (H2
in-memory DB) and stops processing jobs.

Fix: the override config explicitly binds every docker-compose env
var via Typesafe Config's native `${?ENV_VAR}` substitution syntax:

```hocon
docspell.joex {
  jdbc.url = ${?DOCSPELL_JOEX_JDBC_URL}
  jdbc.user = ${?DOCSPELL_JOEX_JDBC_USER}
  jdbc.password = ${?DOCSPELL_JOEX_JDBC_PASSWORD}
  full-text-search.solr.url = ${?DOCSPELL_JOEX_FULL__TEXT__SEARCH_SOLR_URL}
  # ...all 13 env vars used in docker-compose joex section...

  extraction.ocr.tesseract.command.args = ["{{file}}", "stdout", "-l", "bul+eng+rus"]
  convert.tesseract.command.args = ["{{infile}}", "out", "-l", "bul+eng+rus", "pdf", "txt"]
  convert.ocrmypdf.command.args = ["-l", "bul+eng+rus", "--skip-text", "--deskew", "-j", "1", "{{infile}}", "{{outfile}}"]
}
```

Verification: joex log shows `Running external command: ocrmypdf -l
bul+eng+rus ...`.

Revert: remove the volume mount from docker-compose.yml's joex
service and `docker compose up -d joex`.

## Known broken: SMTP / "Send Item via E-Mail" in v0.43.0

The Outgoing E-Mail feature (Send Item via E-Mail, configured in
User Settings → E-Mail Settings → SMTP) is **broken on Docspell v0.43.0**
due to upstream bug [eikek/docspell#3099](https://github.com/eikek/docspell/issues/3099)
(open since 2025-07-05, confirmed by the maintainer).

Symptom: POST `/sec/email/settings/smtp` returns
`Internal error: ERROR: INSERT has more expressions than target columns`
because Doobie generates 13 placeholders but the `useremail` table
has 12 columns. The codec for `MailAddress` (used in `mail_from`)
emits an extra value with no matching column.

The maintainer-suggested manual INSERT workaround successfully writes
the row to the DB, but then the read codec fails identically on GET
(HTTP 500). Both directions broken.

**Workaround used in practice**: skip the SMTP feature entirely.
Outgoing email is done manually — download the PDF attachment from
Docspell, compose a new mail in your Gmail web UI, attach the PDF.
This is a small inconvenience compared to the value of incoming
email ingestion (which works fine — see below).

When eikek/docspell#3099 is fixed in a future release, upgrade and
configure SMTP normally through the UI or `setup_email_ingestion.py`.

## Schema versioning

Bump the `schema_version` in `SCHEMA.md` when adding/removing
folders, tag categories, custom fields, or making breaking changes to
apply scripts. Current: **v1.1** (2026-05-20) — added joex OCR override.
