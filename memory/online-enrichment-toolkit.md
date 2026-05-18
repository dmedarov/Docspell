# Docspell book classifier с online enrichment

> Memory file — създаден 2026-05-19. Описва toolkit-а, който Damian
> подготви paralelно с моите apply скриптове, с external book-metadata
> enrichment през Open Library + Google Books.

## Какво представлява

Отделен Python toolkit, идващ като ZIP — `docspell_book_system_enriched.zip` —
който се разархивира в `/Users/dmedarov/CODING/Docspell/docspell_book_system_enriched/`.
Основният entry point е `docspell_book_classifier.py`.

Различен е от моите `classify_by_name.py` + `apply_reviewed_actions.py` по две неща:
- **Online enrichment** — сверява title + author срещу публични book-metadata
  API-та (Open Library, Google Books) за да получи clean title, authors, year,
  publisher, ISBN13, categories. Полезно, когато местното filename е загадъчно
  (`Olson.pdf`, `klein4.pdf`, `BFFIADITBI.pdf`).
- **Bundled apply mode** — самостоятелна `apply` команда вместо отделен скрипт.

## Privacy guarantees

При `--online-enrich`:
- Към Open Library и Google Books се пращат САМО заглавие и (когато е известен)
  автор — нищо повече.
- Docspell token, парола, OCR текст, attachment байтове, item IDs — НЕ напускат
  локалната машина.
- Резултатите се кешират локално (`out/books-enriched/book-enrichment-cache.json`),
  така че повторен run не атакува отново external API-тата.

## Online flags

```text
--online-enrich                       # включва enrichment
--online-provider both|openlibrary|googlebooks
--online-cache <path>                 # default out/.../book-enrichment-cache.json
--online-max-results <N>              # колко candidate-а на API call
--online-delay <seconds>              # rate-limit между заявки
--online-timeout <seconds>            # HTTP timeout
--online-min-match-score <0..1>       # threshold за accept
--google-books-api-key <key>          # или GOOGLE_BOOKS_API_KEY env var
```

Google Books публичните заявки не изискват Authorization header, но
identification чрез API key или OAuth прави rate limits предсказуеми.

## Workflow

### 1. Разархивиране и smoke test
```bash
cd /Users/dmedarov/CODING/Docspell
unzip /path/to/docspell_book_system_enriched.zip
cd docspell_book_system_enriched
python3 -m py_compile docspell_book_classifier.py
```

### 2. Classify + enrich
```bash
python3 docspell_book_classifier.py classify-csv \
  --input ../out/docspell-actions.csv \
  --out out/books-enriched \
  --online-enrich \
  --online-provider both \
  --online-max-results 5 \
  --online-delay 0.35
```

С API key:
```bash
export GOOGLE_BOOKS_API_KEY="YOUR_KEY"
python3 docspell_book_classifier.py classify-csv \
  --input ../out/docspell-actions.csv \
  --out out/books-enriched \
  --online-enrich \
  --online-provider both
```

### 3. Outputs (всичко в `out/books-enriched/`)

```text
book-actions.csv               — пълно действие per item
book-actions-safe_book.csv     — high-confidence (за direct apply)
book-actions-probable_book.csv — medium confidence (човешки преглед)
book-actions-manual_review.csv — low confidence
book-actions-reject.csv        — не са книги или не са разпознати
book-enrichment.csv            — main metadata file (вижда се най-добре)
book-enrichment-cache.json     — local cache за rerun-ите
book-summary.md                — overview
```

### 4. Enrichment columns

```text
enrichment_source         — openlibrary | googlebooks
enrichment_match_score    — 0..1
enrichment_title          — нормализирано заглавие
enrichment_authors        — semicolon-separated
enrichment_year           — first published year
enrichment_publisher
enrichment_isbn13
enrichment_id             — OL ID или Google volume ID
enrichment_url            — линк към record-а
enrichment_categories     — теми от външния каталог
enrichment_reason         — защо този match беше избран
```

### 5. Apply (dry-run по default)

```bash
python3 docspell_book_classifier.py apply \
  --url https://docspell.medarov.net \
  --account "COLLECTIVE/USER" \
  --actions out/books-enriched/book-actions-safe_book.csv \
  --create-folder \
  --create-missing-tags
```

Реално прилагане:
```bash
python3 docspell_book_classifier.py apply \
  --url https://docspell.medarov.net \
  --account "COLLECTIVE/USER" \
  --actions out/books-enriched/book-actions-safe_book.csv \
  --create-folder \
  --create-missing-tags \
  --apply \
  --confirm APPLY-BOOKS
```

Confirm phrase е `APPLY-BOOKS` (различно от моя `APPLY-LIBRARY`).

## Преди apply: review what Damian asked for

Преди да пусне `--apply`, той иска първо да му покажа за преглед:
- `out/books-enriched/book-enrichment.csv`
- `out/books-enriched/book-actions-safe_book.csv`
- `out/books-enriched/book-summary.md`

Целта е да проверим дали online metadata-то е достатъчно чисто (не accidentally
mapping-ва academic ebook към грешен record).

## Връзка с моя apply

Двата процеса не се чупят, защото:
- Моят apply слага folder=Library + Book:<topic> tags via PUT /taglink — additive.
- Този toolkit също е additive (`--create-missing-tags`).
- Apply скриптовете са idempotent — повторно прилагане не дублира.

Препоръка: ако Damian реши да пусне book-classifier-а след моите 678 items,
енриченият metadata ще се добави като нови tags (напр. ISBN, publisher) без
да премахва съществуващите `doctype:book` или `area:*` категории.

## Какво остава да направя в моя workflow (state към края на тази сесия)

- 240/678 items applied (batches 0, 1, 2, 3)
- 438 items остават в batches 4-11
- Continuing в следваща сесия с command:
  `python3 apply_reviewed_actions.py --apply --confirm APPLY-LIBRARY`
  или продължавам browser apply от batch 4 нататък

Чекпойнт state:
- Folders Library + Personal — created (IDs in browser session)
- 22 tags — created (DIY + Mathematics reused, 20 new under category "Book"
  плюс Certificate без категория)
- Token TTL — около 5 мин default, ще трябва re-login при следваща сесия
