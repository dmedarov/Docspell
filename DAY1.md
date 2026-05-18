# Docspell — Day 1 / Week 1

Кратък оперативен наръчник след bulk-класификацията. Прочети го уморен в 2 ч. през нощта и просто върви по списъка.

## ✓ Това вече е направено

Папките `Library` и `Personal` са създадени, 22 тагове са въведени (20 нови + DIY/Mathematics). 31 български организации са seedнати като correspondents. 670 item-а са класифицирани, 227 от тях имат external metadata в custom fields (ISBN, издател, година). Дубликатите са изчистени, репото е push-нато в GitHub.

## Тази вечер (5 мин)

1. **Document Language.** Влез в Docspell → горе вдясно avatar → Collective Settings → раздел *Document Language*. Постави **English** и натисни Save. Това включва English OCR pipeline за бъдещи качвания — повечето от твоята библиотека е на английски.

2. **TOTP 2FA.** User Settings (avatar → *User Settings*) → раздел *Two Factor Authentication* → Enable. Сканирай QR кода с Aegis / Authy / 1Password. Запиши secret-а ръчно в password manager-а — **Docspell 0.43.0 не генерира backup кодове**. Ако загубиш телефона си, единственото възстановяване е admin reset през `docspell-admin` CLI на сървъра. Това не е страшно (имаш SSH), но го знай предварително.

3. **Visual spot check.** Отвори `https://docspell.medarov.net/app/search` и пусни тези три заявки една след друга, само за да потвърдиш, че миграцията е минала чисто:
   - `folder:Library` — очаквай около **654 item-а**
   - `customfield.book_year>2010` — очаквай **80-100 модерни книги**
   - `inbox:yes` — очаквай **14 item-а** (необработените)

Ако някоя цифра е драстично различна, спри и провери логовете, преди да продължиш.

## Утре (15 мин при сутрешно кафе)

1. **Confirm 14-те inbox items.** Това са кратките или двусмислени имена, които classifier-ът не успя да разпознае. Отвори всеки в UI-я, виж thumbnail-а или първите редове от OCR, и реши папка + тагове ръчно:

   `AI TOP.docx`, `klein4.pdf`, `money.pdf`, `Niva.pdf`, `oct08.pdf`, `Olson.pdf`, `ROOT.docx`, `society.pdf`, `thelaw.pdf`, `Tracy.pdf`, `URBAN.pdf`, `w10342.pdf`, `За Вяра.doc`, `IMG_0158.jpeg`.

   Подсказки: `w10342.pdf` най-вероятно е NBER working paper (Library/Economics); `За Вяра.doc` — Personal; `IMG_0158.jpeg` — Personal или изтрий; `thelaw.pdf` — Bastiat е, Library/Economics; `Olson.pdf` — Mancur Olson, Library/Economics.

2. **Run verification.** От repo-то изпълни `python3 verify_docspell.py`. Това минава health check на API, брои item-ите по папки, проверява за orphan тагове и липсващи correspondents. Ако излезе clean, си готов.

## Следваща седмица (30 мин total)

1. **Sources за upload канали.** Collective Settings → *Sources* → New Source. Създай:
   - `scanner` — с default тег `doctype:scan`, default папка `Personal`
   - `phone` — с default тег `source:phone`

   Всеки Source ти дава отделен upload URL + token. Сложи `scanner` URL-а в скенера, `phone` — в shortcut на телефона.

2. **IMAP за фактури.** Setup mailbox `Docspell/Invoices` в Gmail. В Docspell: User Settings → *E-Mail Settings* → добави IMAP акаунт с **app password** (не основния — той е plain-text в DB-то на Docspell 0.43.0). После Collective Settings → *Scan Mailbox* → нова task с филтър `*.pdf|*invoice*|*фактура*`, schedule веднъж дневно.

3. **dsc CLI.** `brew install dsc`. Конфигурирай с `dsc gen-config` и постави URL + token. Тест: `dsc search 'folder:Library' | head`. Това е твоят локален CLI за всичко скриптируемо.

4. **Weekly backup cron.** Добави crontab entry: `0 3 * * 0 dsc export --target ~/Backups/Docspell-$(date +\%Y\%m\%d).zip`. Това дава пълен export всяка неделя в 3 ч. сутринта. Запази последните 8 седмици, ротирай по-старите.

## Месец 1 (когато имаш 100+ confirmed items)

1. **Auto-tagging.** Collective Settings → *Classifier* → enable за категория `Book`. Задай schedule за daily training в 4 ч. сутринта. Docspell ще започне да предлага тагове върху новите качвания на базата на твоите досегашни confirm-и. Минималният threshold е ~100 confirmed примера, поради което чакаме месец.

2. **Pi-hole / DNS.** Отделен workstream — не пряко свързан с Docspell, но добра хигиена за homelab-а.

## Когато дойде първа реална фактура

5-стъпков workflow за 30 секунди:

1. Forward email-а към Docspell IMAP-а, или upload директно през `scanner` Source.
2. Изчакай ~30 сек. — OCR, language detection и address-book match се случват backgound.
3. Отвори Inbox → click върху item-а.
4. Провери auto-detected correspondent (например *A1 България* — вече е в твоите 31 seedнати орг.). Ако не е разпознат, добави го ръчно от dropdown-а.
5. Сложи таг `doctype:invoice` (създай го предварително за non-book документи) + `area:telecom`, попълни custom field `invoice_amount`, натисни Confirm.

## Cheat sheet — Docspell search query language

Топ 8 заявки за запомняне:

- `tag:Book:Economics` — items с този таг
- `corr:"ДСК Банк"` — items с този correspondent
- `customfield.book_year<1990` — vintage книги
- `content:"central bank"` — full-text търсене
- `date>today;-30d` — последни 30 дни
- `!exist:folder` — items без папка
- `inbox:yes` — чакащи review
- `f.book_isbn=9780070536678` — custom field shorthand

## Стандартни bugs / quirks към запомняне

Три известни проблема, които ще ти спестят час дебъгване:

- `POST /sec/item/{id}/tags` е счупен в 0.43.0 (връща 500). Използвай `PUT /sec/item/{id}/taglink` — той е additive и работи.
- Tag create body-то изисква `{"id":"", "name":"X", "category":"Y", "created":0}` — празното `id` и `created:0` са задължителни, иначе 500.
- Auth token TTL е ~5 мин. За дълги bulk операции прави re-login на всеки 4 мин., иначе ще ти умре в средата на batch-а.
