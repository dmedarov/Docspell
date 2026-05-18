# Docspell book classifier — quickstart за твоя Mac

> Toolkit-ът е разположен в `/Users/dmedarov/CODING/Docspell/docspell_book_system_enriched/`.
> Sandbox-ът на Claude няма internet до Open Library / Google Books, затова
> online enrichment-ът се пуска САМО от твоята машина.

## 0. Verify

```bash
cd /Users/dmedarov/CODING/Docspell/docspell_book_system_enriched
python3 -m py_compile docspell_book_classifier.py   # syntax OK
python3 docspell_book_classifier.py --help          # help OK
```

Файлове:
- `docspell_book_classifier.py` — основният скрипт
- `book_classifier_rules.json` — правилата (online_min_score=0.25 за да задейства enrichment на всички book candidates)

## 1. Първи run — full classify + online enrich

```bash
cd /Users/dmedarov/CODING/Docspell/docspell_book_system_enriched
python3 docspell_book_classifier.py classify-csv \
  --input ../out/docspell-actions.csv \
  --out out/books-enriched \
  --online-enrich \
  --online-provider both \
  --online-max-results 5 \
  --online-delay 0.35 \
  --online-timeout 15
```

Очакваме ~616 items × 2 providers × 0.35s = около 7-8 минути. Кешира се в
`out/books-enriched/book-enrichment-cache.json` — повторен run е секунди.

## 2. Optional: с Google Books API key (по-стабилен rate limit)

Вземи безплатен API key от https://console.cloud.google.com/ (включваш Books API).
След това:

```bash
export GOOGLE_BOOKS_API_KEY="YOUR_KEY"
python3 docspell_book_classifier.py classify-csv \
  --input ../out/docspell-actions.csv \
  --out out/books-enriched \
  --online-enrich \
  --online-provider both
```

## 3. Output files (за review)

```text
out/books-enriched/
  ├── book-actions.csv              ← всичко (master)
  ├── book-actions-safe_book.csv    ← конфиденс ≥ 0.82 (за apply)
  ├── book-actions-probable_book.csv ← 0.65-0.82 (човешки преглед)
  ├── book-actions-manual_review.csv ← 0.45-0.65
  ├── book-actions-reject.csv       ← не са книги
  ├── book-enrichment.csv           ← metadata от Open Library / Google Books
  ├── book-enrichment-cache.json    ← кеш (за rerun)
  └── book-summary.md               ← обобщение
```

## 4. Качи 3-те файла за review

След като run-ът приключи, качи в чата:
- `book-enrichment.csv`
- `book-actions-safe_book.csv`
- `book-summary.md`

Аз ще ги прегледам, ще проверя дали match-овете са свободни от
fаlse-positive-и (напр. "Olson.pdf" → правилно намерен или mismatched
на нещо случайно), и ще ти потвърдя дали е безопасно да пуснеш apply.

## 5. След review — apply

Dry-run първо (по default):

```bash
python3 docspell_book_classifier.py apply \
  --url https://docspell.medarov.net \
  --account library/USER_NAME \
  --actions out/books-enriched/book-actions-safe_book.csv \
  --create-folder \
  --create-missing-tags
```

Реално прилагане:

```bash
python3 docspell_book_classifier.py apply \
  --url https://docspell.medarov.net \
  --account library/USER_NAME \
  --actions out/books-enriched/book-actions-safe_book.csv \
  --create-folder \
  --create-missing-tags \
  --apply \
  --confirm APPLY-BOOKS
```

## Какво коригирах в config-а

- `online_min_score`: 0.45 → **0.25**
  Иначе твоят dataset (повечето base scores 0.30-0.45) ще пропусне enrichment-а
  за всички items. С 0.25 всички candidate-и преминават през Open Library +
  Google Books и enrichment-ът може да повдигне confidence-та им.

- `tag_keywords`: подравнени с твоята съществуваща Docspell taxonomy
  (Economics, Monetary, Banking, History, DIY, Home, Mathematics, Management,
  Project Management, Equipment, etc.) — същите имена като tags-те, които
  моят apply вече създаде, така че двете няма да дублират.

- `target_folder`: Library (твоя избор от по-рано)

## Compatibility с моя apply

Двете системи са additive — нито една не премахва съществуващи tags. Те ще
работят паралелно:
- Аз вече сложих 240/678 items с folder=Library + Book:* tags по filename.
- Твоят `apply` ще добави metadata-base tags (ISBN, year, publisher, external
  category) върху същите items без да чупи нищо.

Confirm phrases са различни:
- Моят: `APPLY-LIBRARY`
- Твоят: `APPLY-BOOKS`
