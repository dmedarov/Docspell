# Docspell taxonomy plan — за конкретния dataset

> Този план е data-driven. Базиран е на 692-та реални items в твоя Docspell
> instance, не на хипотези. Той корекционно допълва оригиналния ти 12-точков
> setup плад с неща, които или не бяха верни, или бяха пропуснати.

## 1. Какво всъщност има в Docspell-а ти

След name-based класификация (`classify_by_name.py`):

| Категория             | Брой   | % от dataset |
| --------------------- | -----: | -----------: |
| Книги / референтни PDF | 676    | 97.7%        |
| Sailing certificates (ORC) | 2 | 0.3%       |
| Снимка / scan         | 1      | 0.1%         |
| Къси неясни filename-и | 13    | 1.9%         |
| **Общо**              | **692** | 100%        |

**Изводът**: dataset-ът е практически *цяла академична библиотека* — финанси,
монетарна теория, икономика, история, DIY, management. Реални business
документи (фактури, договори, банкови извлечения) **нямаш изобщо** в текущия
inbox. Всичко това променя стратегията.

Top area-та според класификатора:

    economics 127 · monetary 62 · banking 53 · management 46 · history 39
    diy 25 · home 14 · mathematics 13 · politics 11 · philosophy 10

## 2. Корекции спрямо първоначалния ти план (от Docspell docs)

Това са нещата, които според официалната Docspell документация трябва да се
направят малко по-различно от това, което беше написал:

**Тагове с категории — Docspell има native поле `category`.**
Не `doctype:invoice` като име на таг. Правилният модел е
`{ name: "invoice", category: "doctype" }`. UI-то рендира категорията като
chip до тага. Затова класификаторът ми вече пише `doctype:invoice` в CSV-то
като shorthand, а `apply_reviewed_actions.py` го превежда правилно при
създаване на тагове.

**Folders не са просто visual scoping — те са permission boundary.**
Всеки folder има owner и members; user-и виждат само items без folder или
items в folders, в които са member. Това подсилва логиката "малко на брой
folders": Personal / Company / Clients / Archive (евентуално Private) са
правилни. Години/доставчици/типове документи **никога** като folders.

**Класификаторът (auto-tagging) учи от текст, не от tags като "Done".**
Whitelist/blacklist е *per-category*, не per-tag. Затова `doctype` и `area`
са правилни кандидати за auto-tag; `status` (todo/paid/waiting) не е, точно
както беше отбелязал. Минимумът за работа е ~100 правилно тагнати items —
след тази миграция ще си над прага за `doctype:book` веднага.

**Tesseract OCR — Bulgarian не е в default Docker image.**
Базовият `docspell/joex` контейнер има `tesseract-ocr-deu` и
`tesseract-ocr-eng`, но не и `bul`. Ако искаш правилен Bulgarian OCR, трябва:
- да build-неш derived image, който прави `apt-get install tesseract-ocr-bul`,
- или да mount-неш tessdata директория с `bul.traineddata`.
Multi-language работи (`bul+eng`).

**Tag explosion е known issue.** Issue #960 в repo-то. Дръж tag count нисък —
favoriзирай custom fields (за numbers/dates/amounts), organizations (за
correspondent), и folder (за permissions).

**Organizations имат address-book auto-detection — попълни email + website.**
Първо NLP модел, после address-book rules. За да work-ват rules-ите, всяка
Org трябва да има website + поне един email.

**Backup minimum set**: DB + file storage (DB-as-blob по default, или
filesystem/S3, ако си го конфигурирал). Solr index не е нужен — възстановим
е от DB. Спирай restserver+joex преди dump.

## 3. Какво да правим сега — конкретни стъпки

### Step 1: Apply на 678 items към Archive

Скриптовете са готови:

    /Users/dmedarov/CODING/Docspell/classify_by_name.py        # офлайн, генерира CSV
    /Users/dmedarov/CODING/Docspell/out/docspell-name-classification.csv  # 692 реда
    /Users/dmedarov/CODING/Docspell/apply_reviewed_actions.py  # прилага spec-а

Команди:

```bash
# 1. Dry-run — не прави нищо, само показва какво ще се случи
python3 apply_reviewed_actions.py

# 2. Реално прилагане — 678 items, ~24 тага със категории, 2 folders
python3 apply_reviewed_actions.py --apply --confirm APPLY-LIBRARY
```

Apply скриптът ще:
- създаде folders `Archive` и `Personal` ако ги няма
- създаде 24 тага с правилни Docspell categories
  (`doctype`, `area` — по 1-3 тага на item)
- сложи всеки от 678-те item в правилния folder
- *добави* tags без да премахва съществуващи
- логне всичко в `out/apply-log.csv`
- НЕ confirm-ва items, НЕ изтрива нищо, НЕ download-ва съдържание

След apply-а ще ти останат 14 items в inbox:
- 13 къси filename-и (Niva.pdf, money.pdf, Olson.pdf, За Вяра.doc и т.н.)
- 1 снимка (IMG_0158.jpeg)

Тези минават през `Inbox → Confirm` ръчно от UI-то — там можеш да отвориш
файла, да видиш съдържанието и да решиш дали е книга, лична снимка или нещо
друго.

### Step 2: Set Document Language

В Docspell: Collective Settings → Document Language → **English**

Не Bulgarian, защото:
1. 95%+ от съдържанието е на английски (учебници по финанси, икономика и пр.)
2. Дори да набавиш `tesseract-ocr-bul` в image-а, мажоритарният език остава en

Когато започнат да влизат истински Bulgarian business документи (фактури),
ще обсъдим Multi-Language setup или derived Docker image с `bul`.

### Step 3: Address-book hygiene — за бъдещето

Преди да започнеш да получаваш истински business documents, създай
Organizations в Docspell за основните доставчици с **website + email**
попълнени:

```text
Banks:    ДСК Банк, UniCredit Bulbank, Postbank, Fibank, ОББ
Telecom:  A1 България, Vivacom, Yettel
Utility:  EVN, ЧЕЗ, Energo-Pro, Топлофикация, Софийска вода, Овергаз
Govt:     НАП, НОИ
Couriers: Speedy, Econt, DHL, DPD
IT:       JetBrains, GitHub, Atlassian, Google, Microsoft, AWS, Canva
```

Класификаторът вече разпознава тези имена в title-и (виж
`CORRESPONDENT_PATTERNS` в `classify_by_name.py`). Когато реални документи
започнат да влизат, ще можем да генерираме автоматично correspondent
suggestions.

### Step 4: Sources — само 2 за начало, не 10

```text
Source: scanner
  Default folder: (none — нека минават през inbox triage)
  Tags: doctype:scan (за да са лесно филтруеми)

Source: email-drop
  Default folder: (none)
  Tags: source:email
```

Това е достатъчно. Повече sources усложняват ingestion без полза.

### Step 5: IMAP scan — една папка, не цялата поща

Направи в Gmail/Outlook отделна папка `Docspell/Inbox`, прехвърляй там
само relevant писма (с PDF attachments или важна business кореспонденция).
IMAP scan task с:

    Folders: INBOX/Docspell/Inbox
    Received since: 168h (1 седмица)
    File filter: *.pdf|*.docx
    Subject filter: *invoice*|*фактура*|*statement*|*извлечение*|*договор*|*contract*

Документацията изрично казва, че IMAP паролите се пазят като plain-text в DB
на Docspell. Затова: **dedicated mailbox или app-specific password**, не
главната ти лична парола.

## 4. Какво да правиш СЛЕД като влязат реални business документи

(Това е за следващия кръг, не сега.)

1. **Re-run триажа** с `docspell_triage.py` — после `classify_by_name.py`
   ще освети какви invoice/contract/statement title-и пристигат.
2. **Възстанови business doctype patterns** в класификатора — той вече има
   готови high-precision patterns за `invoice`, `bank-statement`, `tax`
   и т.н., които НЕ страдат от false-positives върху книги.
3. **Включи auto-tagging за `doctype` категорията** само. Не за `area` (
   защото няма да има достатъчно non-Archive examples). И определено не за
   workflow tags.
4. **Активирай custom fields** за: `invoice_number`, `amount`, `due_date`,
   `vat_period`, `contract_number`. Тези са по-полезни от tags за filter-и
   тип "какво дължа за този месец".

## 5. Backup + security checklist (направи го ПРЕДИ apply-а)

Преди да натиснеш `APPLY-LIBRARY`:

- [ ] DB dump (pg_dump на Docspell-овата база)
- [ ] Snapshot на file storage (volume/директорията)
- [ ] Запиши `docker-compose.yml` или helm values
- [ ] Тестът на restore-а *поне веднъж* — ако не успее, fix-ни го сега

Apply-ът е idempotent (не дублира tags, не сменя folder ако вече е сложен),
но backup е cheap insurance за първи bulk operation.

Long-term security:
- HTTPS зад reverse proxy ✓ (ползваш docspell.medarov.net)
- Силна парола + TOTP 2FA (user settings → enable 2FA)
- Dedicated mailbox за IMAP
- Weekly `dsc export` като secondary backup (app-portable)

## 6. Ползване всеки ден — препоръчителен workflow

```text
1. Документ влиза (scanner, email, ръчен upload).
2. Docspell обработва (OCR, NLP, suggestions).
3. Ти отваряш Inbox филтъра в UI-то.
4. За всеки нов item:
   - Провери title (corrigirай ако скенера/OCR-а е сгрешил)
   - Провери date
   - Постави correspondent (Org, не tag)
   - Постави doctype:* tag
   - Постави area:* tag(ове) ако е смислено
   - При invoice/contract — попълни custom fields
   - Confirm
5. Цел: 0 unconfirmed items в края на деня.
```

5 минути на ден > 300 неподредени документа след месец.

---

## Линкове към източници

- https://docspell.org/docs/webapp/metadata/
- https://docspell.org/docs/webapp/autotagging/
- https://docspell.org/docs/webapp/customfields/
- https://docspell.org/docs/webapp/uploading/
- https://docspell.org/docs/webapp/scanmailbox/
- https://docspell.org/docs/webapp/emailsettings/
- https://docspell.org/docs/query/
- https://docspell.org/docs/joex/file-processing/
- https://docspell.org/docs/install/prereq/
- https://docspell.org/docs/tools/cli/
- https://github.com/eikek/docspell/issues/960 (tag explosion)
- https://github.com/eikek/docspell/issues/942 (OCR languages)
- https://github.com/eikek/docspell/issues/2485 (backup/restore)
