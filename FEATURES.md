# Docspell 0.43.0 — пълно ръководство за collective "library"

> Това е практически наръчник за работа с твоя Docspell на `https://docspell.medarov.net`.
> Минава секция по секция през всяка функция на системата с конкретни UI пътища,
> API endpoint-и, curl примери и приложения към реалния ти dataset
> (~700 PDF-а от лична библиотека: финанси, монетарна политика, икономика, DIY).
>
> Всичко тук е emirически проверено или взето от официалната документация на 0.43.0.
> Където нещо не съм потвърдил на жива система, изрично го казвам.

---

## 0. Базови положения — четка преди да започнеш

Преди всяка curl команда, която изпълняваш срещу `/api/v1/sec/...`, ще ти трябва токен.
Текущата версия е 0.43.0, default token TTL e около 5 минути — тоест не можеш
да хардкодваш токен в скрипт и да го ползваш утре.

```bash
TOKEN=$(curl -s -X POST https://docspell.medarov.net/api/v1/open/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"account":"library/damian","password":"...","rememberMe":false}' \
  | jq -r .token)

# Всяка следваща заявка:
curl -H "X-Docspell-Auth: $TOKEN" https://docspell.medarov.net/api/v1/sec/...
```

Account name винаги е `collective/user`, тоест за теб `library/damian`. Ако пуснеш
`library` сам, login-ът пада.

Админ endpoint-ите (които спират/рестартират global процеси) искат **втори**
header: `Docspell-Admin-Secret: <secret>`. Това е стойност от `application.conf`
(`admin-endpoint.secret`), не парола на потребител. Ако не си го конфигурирал,
admin endpoint-ите са изключени.

Documentation: [https://docspell.org/docs/api/intro/](https://docspell.org/docs/api/intro/)

---

## 1. OCR (Tesseract)

**Защо това е важно за теб:** Цялата ти библиотека вече минала през OCR (или
ще мине при reprocess). Качеството на OCR определя дали `content:"какво-да-е"`
search ще намира думи в книгите.

**Кога Docspell прави OCR.** Веднъж — при processing на attachment. Pipeline:
duplicate check → extract archives → convert to PDF → **text extraction →
OCR fallback** → preview → analyse. Текст се извлича първо native-но от
PDF-а (ако е "истински" текстов слой), и само ако native резултатът е беден,
се пуска OCR върху rasterized страниците. Тоест за повечето ти PDF-и (digital
ebooks) Tesseract изобщо не се извиква — текстовият слой е там.

**Document Language за collective-а.** UI: *Collective Settings → Document
Language*. Стойността тук се подава като `-l` flag на `tesseract` за всички
processing задачи. Multi-language: `bul+eng` синтаксис в полето е валиден.

**Какъв език да сложиш ти.** `eng` (само английски). Причини:
- 95%+ от dataset-а ти е на английски (финанси, икономика, DIY ebooks).
- `bul+eng` зарежда и двата traineddata файла във всеки run — забавя
  процесинга на всяка страница 1.3-1.8x.
- Когато започнат да влизат реални български документи (фактури, договори),
  обмисли преминаване на `bul+eng` или прави второ collective.

**Кои езици идват в официалния Docker image.** Базовият `docspell/joex:0.43.0`
има инсталирани `tesseract-ocr-deu` и `tesseract-ocr-eng` — нищо повече.
Bulgarian (`bul`) НЕ е там по default.

**Ако трябва Bulgarian OCR.** Два пътя:

1. *Derived image* (по-чисто):
   ```Dockerfile
   FROM docspell/joex:0.43.0
   USER root
   RUN apt-get update && apt-get install -y --no-install-recommends \
         tesseract-ocr-bul && rm -rf /var/lib/apt/lists/*
   USER docspell
   ```
2. *Mount на tessdata* (без rebuild):
   ```yaml
   # docker-compose.yml
   joex:
     volumes:
       - ./tessdata:/usr/share/tessdata
   ```
   и хвърли `bul.traineddata` от
   [tessdata_best](https://github.com/tesseract-ocr/tessdata_best) в `./tessdata/`.

После: *Collective Settings → Document Language → Bulgarian* (или сложи
`bul+eng` мулти-език в multi-lang режим).

Documentation: [https://docspell.org/docs/joex/file-processing/](https://docspell.org/docs/joex/file-processing/) и
[https://docspell.org/docs/dev/add-language/](https://docspell.org/docs/dev/add-language/)

---

## 2. Full-text search backend

**Защо е важно:** Това определя как работи `content:` query и колко бързо
връща резултати в 700-итемния ти dataset.

**Двете опции.** Docspell поддържа два FTS backend-а:

- **Solr** (default за official docker-compose) — отделен сервиз на 8983.
  Бърз, добре handle-ва multi-stem за DE/EN.
- **PostgreSQL FTS** (по-нов, активиран през `full-text-search.backend = "postgresql"`).
  Спестява един контейнер, но е по-бавен за големи индекси.

**Кой имаш ти.** Почти сигурно Solr — това е default-ът в официалния compose
stack. Можеш да провериш с `docker ps | grep solr` или като отвориш
`https://docspell.medarov.net/api/info/version` и видиш дали Solr healthcheck
е zelen в operating dashboard-а.

**Кога ще ти трябва rebuild на index-а:**
- След restore от backup, ако си вдигнал DB без Solr volume.
- След ъпгрейд на Docspell, ако changelog-ът казва "FTS schema change".
- Ако `content:foo` връща странни резултати спрямо `name:foo`.

**Командата:**
```bash
curl -X POST \
  -H "Docspell-Admin-Secret: $ADMIN_SECRET" \
  https://docspell.medarov.net/api/v1/admin/fts/reIndexAll
```

Или през CLI (eднoредно): `dsc admin -a $ADMIN_SECRET recreate-index`. Job-ът
тече async през joex; следиш го в *Job Queue* в UI-то.

Documentation: [https://docspell.org/docs/configure/fulltext-search/](https://docspell.org/docs/configure/fulltext-search/)
и [https://docspell.org/docs/configure/admin-endpoint/](https://docspell.org/docs/configure/admin-endpoint/)

---

## 3. PDF/A и searchable PDF

**Защо е важно:** Дългосрочно архивиране и възможност да отвориш PDF-а след
20 години без зависимост от specific reader.

**Какво прави Docspell автоматично.** При processing всеки attachment се
конвертира до PDF/A-2b чрез OCRmyPDF + Ghostscript. Резултатът е PDF с
вграден OCR text layer (тоест searchable PDF в Acrobat-смисъл) плюс PDF/A
metadata за дългосрочно архивиране.

**Къде живеят двете копия.** Docspell пази два файла на attachment:
- **Original** (`attachment_source` table) — точният файл, който си качил.
  Винаги се пази, никога не се пипа.
- **Converted PDF** (`attachment` table) — PDF/A с OCR layer.

В UI: *детайл на item → конкретен attachment → бутон "Original"* сваля
оригиналния файл, по default download бутонът дава converted PDF/A.

**Как да провериш PDF/A валидността:**
```bash
# Сваляш converted PDF
curl -H "X-Docspell-Auth: $TOKEN" \
  -o converted.pdf \
  https://docspell.medarov.net/api/v1/sec/attachment/$ATTACH_ID

# Веrify
pdfinfo converted.pdf | grep -i 'PDF.*version\|conformance'
# Очаквай: PDF version: 1.7, PDF/A conformance: PDF/A-2b
```

**Гоча.** За PDF-и, които вече имат текстов слой, OCR не се преизпълнява —
просто се прави PDF/A обвивка. Това е защо повечето ти ebooks се
конвертират за секунди вместо минути.

Documentation: [https://docspell.org/docs/dev/adr/0015-convert-pdf-files/](https://docspell.org/docs/dev/adr/0015-convert-pdf-files/)

---

## 4. Tags, folders, custom fields — дълбок дайв в data model-а

### 4.1 Tags и категории

**Защо е важно:** Tag taxonomy-та ти е fundamentum за filter-ите. Лош taxonomy
= 200 тага, които никой не помни.

Docspell има *native* поле `category` на тага. Не `doctype:invoice` като име
на тага — а:

```json
{ "id": "...", "name": "invoice", "category": "doctype" }
```

UI-то рендира категорията като малък chip до името на тага. В query language:
`tag:invoice` (по име) или `cat:doctype` (по категория).

**Текущи категории в твоя collective.** Според direct API проверка:
- `Book` — DIY, Mathematics (и др. под-тагове)
- `Smart home` — Watering
- *(без категория)* — BOSH, Heating, HOME, "user manual"

**Дизайн препоръка.** Категориите ти трябва да са orthogonal — тоест един
item може да има тагове от **различни** категории и да не им се удрят
семантиките. Здравословен набор:

| Категория | Примерни тагове | За какво |
|-----------|-----------------|----------|
| `doctype` | book, invoice, contract, manual, statement, receipt | *Какво е* документът |
| `area` | finance, monetary, banking, diy, home, mathematics | *За какво е* |
| `status` | todo, paid, waiting, archived | Workflow състояние |
| `Book` | DIY, Mathematics, Finance | Sub-classification на книги |

`status` тагове **никога** не трябва да са в classifier whitelist (виж §13).

**Кога няколко категории vs subcategories.** Docspell няма nested tags
(issue #413 е отворен от години). Това, което ползваш като "поднаправление"
(`Book` категорията със стойности `DIY`, `Mathematics`) е напълно правилно.
Не се опитвай да правиш `doctype:book.finance` като име на таг — counter
към native модела.

### 4.2 Folders

**Защо е важно — и опасно.** Folder в Docspell има owner и members.
User-ите виждат само items без folder ИЛИ items в folders, в които са
member. Това е *access control*, не *secrecy*.

**Какво НЕ е folder:**
- Не е cryptographic wall. Ако някой има `item_id` (например view някога,
  логи на nginx), API call към `/api/v1/sec/item/{id}` връща съдържанието.
  Folder ограничава **search/list**, не direct fetch по ID.
- Не е hierarchy. Няма "Personal/Finance/2024" — folder-ите са плоски.

**Препоръка за теб.** Малко folders: `Archive` (за 678-те книги), `Personal`,
евентуално `Private` (за документи с лични данни). Никога *години, доставчици,
типове документи* като folder — това е работа на tags/correspondent/date.

**API за смяна на folder на item.** Empирично потвърдено:
```bash
curl -X PUT -H "X-Docspell-Auth: $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"id":"<folder_id>"}' \
  https://docspell.medarov.net/api/v1/sec/item/$ITEM_ID/folder
```

Body-то е `{"id":"<folder_id>"}`. Празен `id` (`""`) маха folder-а.

### 4.3 Custom fields

**Защо е важно:** Tags са per/yes-no. Custom fields са per/value. За
`amount: 234.56 EUR` няма как да го запишеш като таг — нужно е money поле.

Five типа:

| Тип | За какво | Как се рендира |
|-----|----------|----------------|
| `text` | свободен низ | input box |
| `number` | произволно число (numeric) | numeric input; в search summary показва sum/avg/min/max |
| `money` | сума + валута | две полета; в summary дава sum по валута |
| `bool` | true/false | checkbox |
| `date` | дата | date picker, ползва се за filter `f.<name>:` |

**За твоя случай.** В момента имаш само книги, тоест custom fields не са
critical. Но веднага щом тръгнат фактури:

- `invoice_number` — text
- `amount` — money
- `due_date` — date
- `vat_period` — text (`2024-Q3`)
- `serial_number` — text (за гаранции на уреди)
- `contract_number` — text

**Гоча.** Имена на custom fields трябва да са valid identifiers — без
интервали, без кирилица в името. Label-ът може да е български,
*name*-ът е latin lowercase.

API за set value на item:
```bash
curl -X PUT -H "X-Docspell-Auth: $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"field":"amount","value":"234.56"}' \
  https://docspell.medarov.net/api/v1/sec/item/$ITEM_ID/customfield
```

Documentation: [https://docspell.org/docs/webapp/metadata/](https://docspell.org/docs/webapp/metadata/) и
[https://docspell.org/docs/dev/adr/0016-custom-fields/](https://docspell.org/docs/dev/adr/0016-custom-fields/)

---

## 5. Sources — публични upload endpoint-и

**Защо е важно:** Source е anonymous URL, в който каквото изсипеш, влиза в
твоя collective с предефинирани folder/tags/priority. Така scanner или
телефон могат да push-ват без да знаят твоята парола.

**UI път:** *Collective Settings → Sources → New Source*.

Полета:
- `name` — display; трябва да е unique per collective.
- `description` — за теб самия.
- `folder` — default folder; запълва се ако request-ът не даде.
- `priority` — `low`/`high`; контролира кога joex ще процесира.
- `tags` — fallback tags, ако request-ът не даде.
- `enabled` — booleв switch.

После получаваш URL вида:
`https://docspell.medarov.net/app/upload/<long-id>`

**Препоръка за теб — два source-а, не десет.**

1. **scanner**
   - Default folder: *(none — нека минават през inbox)*
   - Tags: `doctype:scan`
   - Priority: `low`
2. **phone**
   - Default folder: *(none)*
   - Tags: `source:phone`
   - Priority: `low`

API create:
```bash
curl -X POST -H "X-Docspell-Auth: $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "source": {
      "id": "",
      "abbrev": "scanner",
      "description": "ScanSnap workflow",
      "counter": 0,
      "enabled": true,
      "priority": "low",
      "folder": null,
      "fileFilter": null,
      "language": null,
      "attachmentsOnly": false
    },
    "tags": {"items": []}
  }' \
  https://docspell.medarov.net/api/v1/sec/source
```

Документация: [https://docspell.org/docs/webapp/uploading/](https://docspell.org/docs/webapp/uploading/) и
[https://docspell.org/docs/api/upload/](https://docspell.org/docs/api/upload/)

---

## 6. IMAP / e-mail import

**Защо е важно:** Един от най-полезните pipeline-и — Docspell сам fetcha
писмата с PDF attachments и ги внася като items.

**Workflow:**

1. *User Settings → E-Mail Settings → IMAP → New connection.*
   Hostname, port, ssl, username, password. Test connection.
2. *User Settings → Scan Mailbox Task → New task.*
   - **IMAP connection:** избираш току-що създадената.
   - **Folders:** `INBOX/Docspell` (една специфична папка, не root INBOX).
   - **Received since:** `168` часа (1 седмица); по-малко = по-чести retries.
   - **File filter:** glob над attachment names, напр. `*.pdf|*.docx`.
   - **Subject filter:** regex над subject; за теб:
     `*invoice*|*фактура*|*statement*|*извлечение*|*договор*|*contract*`
   - **Target folder:** Docspell folder, в който да паднат items-ите
     (например `Inbox` или `Personal`).
   - **Action on done:** обикновено `move to folder` (Docspell мейл папка,
     не Docspell `folder`) — премества прочетените мейли извън scan папката.

**Гоча.** "Folders" в task config-а са **IMAP folders на mail сървъра**, не
Docspell folders. Не ги бъркай — UI-то ги показва в един и същ диалог.

**SIGURNOST — критично.** IMAP паролите се пазят **plain-text в Docspell DB**.
Това е документирано официално. Затова:

1. **Никога** главната парола на личния mail account.
2. Gmail/Google Workspace → използвай *app password* (16 знака, scoped).
3. Или dedicated mailbox (`docspell@medarov.net`), който и да compromise, не
   засяга main inbox-а.
4. Алтернатива: OAuth2 — Docspell-овият IMAP integration поддържа XOAUTH2;
   в IMAP settings включваш OAuth2 и в полето "парола" слагаш access token.

Documentation: [https://docspell.org/docs/webapp/emailsettings/](https://docspell.org/docs/webapp/emailsettings/) и
[https://docspell.org/docs/webapp/scanmailbox/](https://docspell.org/docs/webapp/scanmailbox/)

---

## 7. REST API — Bulgarian quickstart

**Защо е важно:** Bulk операции (както твоят apply на 678 items) минават
само през API. UI-то не handle-ва multi-select добре.

15-те endpoint-а, които реално ще ползваш:

| # | Method + path | Описание |
|---|---|---|
| 1 | `POST /api/v1/open/auth/login` | Login, връща `token` |
| 2 | `GET  /api/info/version` | Версия (без auth) |
| 3 | `POST /api/v1/sec/item/search` | Search items по query |
| 4 | `GET  /api/v1/sec/item/{id}` | Детайл на item |
| 5 | `PUT  /api/v1/sec/item/{id}/folder` | Set folder |
| 6 | `PUT  /api/v1/sec/item/{id}/taglink` | Add tags (без replace!) |
| 7 | `PUT  /api/v1/sec/item/{id}/tags` | REPLACE tags |
| 8 | `PUT  /api/v1/sec/item/{id}/corrOrg` | Set correspondent org |
| 9 | `PUT  /api/v1/sec/item/{id}/notes` | Set notes |
| 10 | `PUT  /api/v1/sec/item/{id}/name` | Set item name |
| 11 | `PUT  /api/v1/sec/item/{id}/duedate` | Set due date |
| 12 | `GET  /api/v1/sec/folder` | List folders |
| 13 | `POST /api/v1/sec/folder` | Create folder |
| 14 | `GET  /api/v1/sec/tag` | List tags |
| 15 | `POST /api/v1/sec/tag` | Create tag |

### 1. Login

```bash
curl -X POST https://docspell.medarov.net/api/v1/open/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"account":"library/damian","password":"$PASS","rememberMe":false}'
```
Gотча: токенът е валиден около 5 минути по default. За дълги bulk скриптове
прави re-login на всеки ~3 мин.

### 2. Version

```bash
curl https://docspell.medarov.net/api/info/version
```
Без auth. Полезно за health-check.

### 3. Item search

```bash
curl -G -H "X-Docspell-Auth: $TOKEN" \
  --data-urlencode 'q=tag:invoice date>2024-01-01' \
  --data-urlencode 'limit=50' \
  https://docspell.medarov.net/api/v1/sec/item/search
```
Гоча: `q` ползва Docspell query language (§16), не SQL-like синтаксис.

### 4. Item detail

```bash
curl -H "X-Docspell-Auth: $TOKEN" \
  https://docspell.medarov.net/api/v1/sec/item/$ITEM_ID
```

### 5. Set folder

```bash
curl -X PUT -H "X-Docspell-Auth: $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"id":"<folder_id>"}' \
  https://docspell.medarov.net/api/v1/sec/item/$ITEM_ID/folder
```
**Empirически потвърдено** — body-то е точно `{"id":"<folder_id>"}`. Празен
string маха folder-а.

### 6. Add tags (PUT taglink) — НЕ replace

```bash
curl -X PUT -H "X-Docspell-Auth: $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"items":["tag_id_1","tag_id_2"]}' \
  https://docspell.medarov.net/api/v1/sec/item/$ITEM_ID/taglink
```

**Empирически потвърдено:**
- PUT `/taglink` *добавя* tags към съществуващите. ✓ Работи.
- POST `/sec/item/{id}/tags` **е счупен в 0.43.0** — връща 500. Не го ползвай.
- PUT `/sec/item/{id}/tags` — *замества* всички tags. Опасно за bulk; ползвай
  само ако точно това искаш.

### 7. Replace tags

```bash
curl -X PUT -H "X-Docspell-Auth: $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"items":["tag_id_only_one"]}' \
  https://docspell.medarov.net/api/v1/sec/item/$ITEM_ID/tags
```

### 8. Set correspondent org

```bash
curl -X PUT -H "X-Docspell-Auth: $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"id":"<org_id>"}' \
  https://docspell.medarov.net/api/v1/sec/item/$ITEM_ID/corrOrg
```

### 9. Notes

```bash
curl -X PUT -H "X-Docspell-Auth: $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"text":"Markdown notes here"}' \
  https://docspell.medarov.net/api/v1/sec/item/$ITEM_ID/notes
```

### 10. Item name

```bash
curl -X PUT -H "X-Docspell-Auth: $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"text":"Нова название"}' \
  https://docspell.medarov.net/api/v1/sec/item/$ITEM_ID/name
```

### 11. Item due date

```bash
curl -X PUT -H "X-Docspell-Auth: $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"date":1735689600000}' \
  https://docspell.medarov.net/api/v1/sec/item/$ITEM_ID/duedate
```
Гоча: датите са unix-millis (UTC), не ISO 8601.

### 12. List folders

```bash
curl -H "X-Docspell-Auth: $TOKEN" \
  https://docspell.medarov.net/api/v1/sec/folder
```

### 13. Create folder

```bash
curl -X POST -H "X-Docspell-Auth: $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"id":"","name":"Archive","created":0}' \
  https://docspell.medarov.net/api/v1/sec/folder
```
**Empirически потвърдено** body shape — `id:""` и `created:0` ги попълва
сървърът. Ако пропуснеш `created`, получаваш 400.

### 14. List tags

```bash
curl -H "X-Docspell-Auth: $TOKEN" \
  https://docspell.medarov.net/api/v1/sec/tag
```

### 15. Create tag

```bash
curl -X POST -H "X-Docspell-Auth: $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"id":"","name":"invoice","category":"doctype","created":0}' \
  https://docspell.medarov.net/api/v1/sec/tag
```
**Empirически потвърдено** — точно това е работещият body shape за 0.43.0.
`category` е optional (празен низ е валиден); `id` и `created` са required
като placeholder-и.

Допълнителни полезни endpoint-и:

- `POST /api/v1/sec/organization` — create org (correspondent).
- `GET  /api/v1/sec/organization` — list orgs.
- `GET  /api/v1/sec/source` / `POST` — list/create sources.
- `POST /api/v1/sec/email/sendItems` — изпрати item през SMTP.
- `POST /api/v1/sec/usertask/scanmailbox/startonce/{id}` — trigger IMAP scan.
- `POST /api/v1/sec/collective/classifier/startonce` — trigger classifier
  training сега, без да чакаш schedule.

Documentation: [https://docspell.org/openapi/docspell-openapi.html](https://docspell.org/openapi/docspell-openapi.html)

---

## 8. SSO / OIDC

**Защо е важно (или не):** Ти си single user, нямаш Keycloak — пропусни сега.
Но ако някога интегрираш с self-hosted Keycloak/Authentik, ето къде да гледаш.

Per-server конфиг в `application.conf`:
```hocon
docspell.server.openid = [
  {
    enabled = true
    provider {
      provider-id = "keycloak"
      client-id = "docspell"
      client-secret = "..."
      scope = "profile"
      authorize-url = "https://keycloak.example.com/realms/home/protocol/openid-connect/auth"
      token-url     = "https://keycloak.example.com/realms/home/protocol/openid-connect/token"
      user-url      = "https://keycloak.example.com/realms/home/protocol/openid-connect/userinfo"
      logout-url    = "..."
    }
    user-key = "preferred_username"
    collective-key = "lookup:groups"
  }
]
```

Това е *server-level* конфиг, не collective. Засяга всички collectives на
инстанцията. Local accounts и OIDC accounts могат да съществуват паралелно;
по default Docspell ги третира като различни (за да не може external IdP
hijack-ва local account със same username).

Documentation: [https://docspell.org/docs/configure/authentication/](https://docspell.org/docs/configure/authentication/)

---

## 9. 2FA (TOTP)

**Защо е важно:** Твоят Docspell е public-facing на medarov.net. TOTP е
минимална хигиена.

**UI път за enable.** *User Settings → Two Factor Authentication → Activate
two-factor authentication.* Появява се QR код. Сканираш с Aegis / Authy /
Google Authenticator. Ако не можеш да сканираш QR-а, бутон "eye icon" показва
secret-а в текст — копираш ръчно. Влизаш 6-цифрен код за потвърждение.
Готово.

**Login flow след това:** username + password → 6-цифрен код от authenticator.

**Backup codes — внимание.** Docspell **НЕ** генерира backup кодове в 0.43.0.
Това е known limitation. Стратегия:

1. Запиши TOTP secret-а ръчно при setup (бутонът "eye icon") и го пази
   в Bitwarden/KeePass secure note. Така ако телефонът ти отиде, можеш да
   възстановиш в нов authenticator app.
2. Или enroll два authenticator app-а с един и същ secret (Aegis + Authy
   на различни устройства).

**Ако загубиш всичко.** Recovery = admin reset през сървъра. На host машината:
```bash
docker exec -it docspell-restserver dsc admin -a $ADMIN_SECRET disable-2fa damian/library
```
Това reset-ва 2FA-то на user-а. Изисква admin secret, тоест трябва да имаш
SSH достъп до сървъра.

Documentation: [https://docspell.org/docs/webapp/totp/](https://docspell.org/docs/webapp/totp/)

---

## 10. Public share links

**Защо е важно:** Един клик — изпращаш на приятел read-only link към
конкретни items или query, без той да има акаунт.

**UI път:** *Items search → филтрирай каквото искаш да споделиш → бутон
"Share" (горе вдясно)*. Полета:
- `name` — за теб самия.
- `query` — query language израз (по default current search).
- `password` — optional, но **strongly recommended** (изпрати го през
  различен канал — Signal, не през същия email където е линка).
- `publish until` — **задължително** поле, не може forever. Сложи края на
  годината или дата на конкретно събитие.

После UI показва URL `https://docspell.medarov.net/app/share/<long-id>`.

**Конкретен use case за теб.** ORC сертификатите за платноходката. Триаж-ът
показва `BUL-12345-orc-sailing.pdf` и още един ORC item. Като ти трябва да
ги пратиш на crew за регата:

1. Search: `tag:sailing` (или каквото има ORC tag-а).
2. Share → name `ORC2026Q3` → password `xxx` → publish until 2026-09-30.
3. Изпращаш на crew-то: link през Signal, парола през друг канал.

**Security caveats:**
- URL-ът е long random, но не е cryptographic guarantee. Ако се leak-не в
  Telegram/email, всеки с линка достъпва (затова парола).
- Read-only — receiver не може да модифицира.
- `publish until` е hard cutoff, проверява се при всеки request.
- Можеш да revoke share по всяко време — *User Settings → Shares → Delete*.

Documentation: [https://docspell.org/docs/webapp/share/](https://docspell.org/docs/webapp/share/)

---

## 11. CLI (dsc)

**Защо е важно:** Watch-folder workflow за scanner-а ти + scriptable bulk
operations. UI-то не handle-ва 678-item bulk моите apply скриптове и `dsc`
са твоите варианти.

**Installation.**

Опция А (recommended) — direct binary release:
```bash
# macOS arm64
curl -L -o /usr/local/bin/dsc \
  https://github.com/docspell/dsc/releases/latest/download/dsc_aarch64-apple-darwin
chmod +x /usr/local/bin/dsc
```

Опция Б — Docker:
```bash
docker run --rm -it -v $HOME/.config/dsc:/root/.config/dsc \
  docspell/dsc:latest <command>
```

**Първи login и config файл.**
```bash
dsc write-config-file        # създава ~/.config/dsc/config.toml
$EDITOR ~/.config/dsc/config.toml
# смени docspell_url на https://docspell.medarov.net
dsc login                    # interactive prompt за credentials
```

`~/.config/dsc/config.toml` е plain text — паролата НЕ се пази, само
session token-ът. Token-ът се refresh-ва автоматично.

**Командите, които реално ще ползваш:**

```bash
# Single file upload
dsc upload ~/Downloads/invoice.pdf

# Whole folder, recursive
dsc upload --traverse ~/Documents/to-upload/

# Watch folder за scanner workflow
dsc watch -r ~/scans/

# Search items
dsc search 'tag:invoice date>2024-01-01'

# Download item (originals + converted)
dsc download <item_id> -o ~/recover/

# Full export (your offline backup)
dsc export --all -o ~/docspell-export-$(date +%F)/

# Help
dsc help
dsc upload --help
```

**Watch folder workflow за scanner-а ти.**

1. ScanSnap (или каквото имаш) export-ва в `~/scans/incoming/`.
2. Терминал стартира `dsc watch -r ~/scans/incoming/`.
3. При нов файл — `dsc` го uploadва автоматично, изтрива го локално
   (с `--delete` flag) и продължава да слуша.
4. Алтернатива: scanner-ът target-ва Source URL (виж §5) и въобще не минава
   през `dsc`. По-малко moving parts.

**Гоча.** `dsc watch` detect-ва само file creation, не file modification.
Ако scanner-ът ти временно записва `temp.pdf` и после го rename-ва на
`scan-2026-05-18.pdf`, watch-ът ще тригерне само веднъж — на rename-а.
Това обикновено е това, което искаш.

Documentation: [https://docspell.org/docs/tools/cli/](https://docspell.org/docs/tools/cli/)

---

## 12. Android client

**Защо е важно:** Снимаш фактура от ресторант с телефона → share menu →
Docspell → пристига в твоя collective. По-удобно от email.

**App name:** "Docspell Share" (от тима `docspell/android-client`,
maintainer Eike Kettner). Не "Docspell Android".

**Дистрибуция.** Само **F-Droid** — НЕ е на Google Play.
- F-Droid: [https://f-droid.org/packages/org.docspell.docspellshare/](https://f-droid.org/packages/org.docspell.docspellshare/)
- GitHub releases (APK directly):
  [https://github.com/docspell/android-client/releases](https://github.com/docspell/android-client/releases)

**Setup на телефона.**

1. Инсталираш Docspell Share от F-Droid.
2. Отваряш app-а → Settings → Add Source.
3. Поставяш URL на Source-а, който си създал в §5 (например
   `https://docspell.medarov.net/app/upload/<id>` за "phone" source-а).
4. Готово. От всеки app — share button → Docspell Share → файлът заминава.

**Какво приложението може:**
- Upload през Share intent (от camera, gallery, PDF viewer-и, gmail).
- Multiple sources (например "phone" и "scanner-mobile").
- НЕ може да browse-ва, търси, тага. Това е strictly upload client.

**Гоча.** Приложението не login-ва с парола — работи само със source URL.
Това е feature: телефонът не държи credentials към твоя collective.

Documentation: [https://docspell.org/docs/tools/android/](https://docspell.org/docs/tools/android/)

---

## 13. Auto-tagging / classifier

**Защо е важно:** Когато реални invoices/contracts тръгнат да влизат,
classifier-ът автоматично ще suggest-ва tags и correspondent на база
текста им — без ръчен triage.

**Как работи.** Docspell тренира per-category класификатор от текста на
твоите *вече tagged* items. Минимум за смислени резултати: ~100 examples
per category. Тренировките се пускат по schedule (UI: *Collective Settings →
Classifier Settings*).

**Whitelist vs blacklist.** Whitelist е по-добрият модел — изрично казваш
кои категории да се учат. Blacklist е inverse. Защо това е важно:
ако имаш категория `status` с тагове като `done`, `paid`, `archived`,
*не искаш* класификаторът да учи от тях — `done` се добавя ръчно като
workflow, не е function на текста.

**За теб конкретно.**

След apply-а на 678-те items към `Book` категорията:
1. *Classifier Settings → enable*
2. Whitelist: `Book`, `doctype`, `area`
3. Blacklist: (празно)
4. Schedule: `Calendar.atFirstSunday("UTC", 4, 0)` (примерно — седмично).

После може да trigger-неш ръчно:
```bash
curl -X POST -H "X-Docspell-Auth: $TOKEN" \
  https://docspell.medarov.net/api/v1/sec/collective/classifier/startonce
```

Job-ът ще се появи в *Job Queue* като `learn-classifier-<collective>`.
Очаквай 1-5 минути на 700 items.

**Гоча.** Класификаторът добавя tags само на items, които **нямат** tags
от тренираните категории. Тоест existing 678-те book-ове няма да си променят
tags след training. Новопостъпили items ще получават suggested tags
автоматично.

Documentation: [https://docspell.org/docs/webapp/autotagging/](https://docspell.org/docs/webapp/autotagging/)

---

## 14. Notifications

**Защо е важно:** "Кажи ми когато нов item падне в Inbox" или "напомняй ми
седмица преди due date на договори".

**Каналите.** Docspell 0.43.0 поддържа:
- **E-Mail** — през конфигуриран SMTP connection (User Settings → E-Mail Settings → SMTP).
- **Matrix** — нужен е room ID, access token, home server URL.
- **Gotify** — push notifications към себе си; URL + app secret.
- **Generic HTTP** — POST към webhook endpoint с JSON body.

**Setup workflow.**

1. *User Settings → Notification Channels → New Channel.* Избираш тип, попълваш данни. Test.
2. *User Settings → Notification Hooks → New Hook.* Свързваш channel + event
   (например `TagsChanged`, `ItemSelection`, `JobDone`, и т.н.).

**Конкретни recipes за теб.**

*Due-date напомняния:*
- Hook type: *Periodic Query*
- Query: `due<today;-14d` (всички items с due date в следващите 14 дни)
- Channel: твоят email
- Schedule: ежедневно сутрин

*Notify on new inbox item:*
- Hook type: *Item Selection*
- Trigger: `JobDone` или filter `inbox:yes`
- Channel: Matrix room или Gotify

Documentation: [https://docspell.org/docs/webapp/notification/](https://docspell.org/docs/webapp/notification/)

---

## 15. Notes и inline изображения

**Защо е важно:** Item-ите имат свободно поле *Notes*, Markdown-съвместимо.

UI: отвори item → бутон *Edit Notes* → пишеш Markdown.

Featurите:
- Headings, lists, bold/italic, links — стандартен Markdown.
- Code fences.
- **Inline images** — `![alt](https://...)` работи, но Docspell не upload-ва
  изображенията автоматично, реферира external URL-и.
- Notes съдържанието влиза в full-text search index-а — т.е. `content:foo`
  ще намира думи от notes.

Use case за теб: на DIY книги слагай notes тип "Прочетени стр. 50-78, важна
формула на стр. 134". На фактури — "Платено през ДСК на 2024-08-12".

API за set:
```bash
curl -X PUT -H "X-Docspell-Auth: $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"text":"**Title**\n- bullet"}' \
  https://docspell.medarov.net/api/v1/sec/item/$ITEM_ID/notes
```

---

## 16. Search query language — пълна референция

**Защо е важно:** Това е езикът, който всяко search box, API call и
notification hook ползва. Master-неш ли го — целият Docspell se отваря.

### Базови оператори

Един израз = поле + оператор + стойност. Множество изрази → AND по default.
`(...)` за grouping. Префикс `!` инвертира.

| Оператор | Семантика | Пример |
|----------|-----------|--------|
| `=` | Точно равно (или "всички от") | `tag=invoice,contract` (има и двата) |
| `:` | "Поне един от" | `tag:invoice,contract` |
| `<` `>` `<=` `>=` | Числа/дати | `date>2024-01-01` |
| `~=` | In-operator, alias за `:` | `corr~=DSK,UniCredit` |
| `!` | Negation | `!tag=done` |

`AND` (default) — `tag:invoice corr:DSK`. `OR` — `(tag:invoice OR tag:receipt)`.

### Полета

| Поле | Значение | Пример |
|------|----------|--------|
| `tag:` / `tag=` | По име на таг | `tag:invoice` |
| `cat:` / `cat=` | По tag category | `cat:doctype` |
| `corr:` | Correspondent (org или person) | `corr:НАП` |
| `concPerson:` | "Concerning person" | `concPerson:Иван` |
| `equip:` | Concerning equipment | `equip:Server1` |
| `folder:` | Folder name | `folder:Archive` |
| `content:` | FTS search в attachment text + name + notes | `content:"капиталови пазари"` |
| `names:` | Item name + attachment name | `names:invoice-2024` |
| `name:` | Само item name | `name:"Geoeconomics"` |
| `date:` | Item date (date или range) | `date:2024-08-12` |
| `due:` | Due date | `due<today;30d` |
| `created:` | Created in Docspell | `created>2026-01-01` |
| `customfield.<name>:` | Custom field value | `customfield.amount>100` |
| `f.<name>:` | Shorthand за customfield | `f.amount>100` |
| `attach.count:` | Брой attachments | `attach.count>2` |
| `attach.length:` | Размер на attachment (bytes) | `attach.length>1000000` |
| `inbox:yes` / `inbox:no` | Дали е в inbox | `inbox:yes` |
| `direction:` | `incoming` / `outgoing` | `direction:incoming` |
| `state:` | Item state | `state:confirmed` |
| `exist:<field>` | Полето има стойност | `exist:folder` |
| `!exist:<field>` | Полето НЕ е попълнено | `!exist:folder` |

### Дати — специален синтаксис

- ISO date: `2024-08-12`
- `today`, `yesterday`
- Relative ranges: `today;-30d` (последните 30 дни)
- Year/month shortcuts: `2024-08`, `2024`

Range: `date:2024-01-01;2024-12-31` (item date в този интервал).

### Полезни comьоси за теб

```
# Книги, които *нямат* folder (escape detection)
cat:Book !exist:folder

# Всичко за финансите от 2024 насам
(tag:finance OR cat:area) date>2024-01-01

# Custom field филтър
f.amount>500 corr:UniCredit

# Items с due date в следващите 14 дни
due<today;14d

# Inbox triage queue
inbox:yes

# Намери дубликат на geoeconomics книга
name:"Geoeconomics"
```

Documentation: [https://docspell.org/docs/query/](https://docspell.org/docs/query/)

---

## 17. Backup & restore

**Защо е важно:** Имаш 700 PDF-а с лична библиотека. Disk failure без
backup = реално загуба на седмици curation.

### Минимален set за backup

1. **Database** (PostgreSQL).
2. **File storage** — където живеят actual PDF файловете.
   В default docker-compose това е `docspell_files` volume (DB-as-blob) или
   `./files/` директория, ако си конфигурирал `filesystem` storage backend.
3. **(Optional)** Solr index. Не e критичен — restore от DB може да
   re-index-не през `/api/v1/admin/fts/reIndexAll`.

### Online dump (с running services)

```bash
# DB dump (Postgres)
docker exec docspell-db pg_dump -U docspell docspell \
  > backup/docspell-$(date +%F).sql

# File storage (ако е filesystem backend)
tar czf backup/files-$(date +%F).tar.gz ./files/

# Или ако е docker volume:
docker run --rm -v docspell_files:/data -v $(pwd)/backup:/backup alpine \
  tar czf /backup/files-$(date +%F).tar.gz -C /data .
```

**Caveat:** Online dump на active DB може да хване in-flight transaction.
За max-consistency:

```bash
docker compose stop restserver joex
# ... dump-ваш ...
docker compose start restserver joex
```

Spirring services за ~30 секунди обикновено е acceptable за single-user
collective.

### Restore

```bash
# Spri всичко
docker compose down

# Restore DB
docker compose up -d db
sleep 5
docker exec -i docspell-db psql -U docspell -d docspell < backup/docspell-2026-05-18.sql

# Restore files
tar xzf backup/files-2026-05-18.tar.gz -C ./files/

# Restart
docker compose up -d
```

После: `dsc admin -a $ADMIN_SECRET recreate-index` за пресъздаване на FTS.

### Secondary backup: dsc export

```bash
dsc export --all -o ~/docspell-export-$(date +%F)/
```

Това е *application-level* backup — сваля original файлове + metadata като
JSON. Полезно като *independent* копие, което не зависи от Docspell DB
schema. Ако Docspell някога ти направи troublesome upgrade, можеш да
re-upload-неш export-а в нова инстанция.

### Преди upgrade

```bash
# 1. Backup (DB + files)
./backup-script.sh

# 2. dsc export като secondary
dsc export --all -o ~/pre-upgrade-export/

# 3. Прочети changelog
# https://github.com/eikek/docspell/blob/master/Changelog.md

# 4. Note: НЯМА downgrade path. След upgrade DB schema не е reversible.
docker compose pull
docker compose up -d
```

Documentation: [https://docspell.org/docs/install/changelog/](https://docspell.org/docs/install/changelog/)

---

## 18. Операционни checklist-и

### Ежедневно (5 мин)

1. Отвори UI → филтър `inbox:yes`.
2. На всеки item: open → check съдържание → set folder + tags → бутон
   **Confirm**.
3. След като е празно — затваряш.

В момента имаш ~14 items в inbox (apply скриптът остави къси filename-и и
снимка за ръчен triage). Тези минават през ежедневния flow.

### Седмично

```bash
# 1. Backup
./backup-script.sh

# 2. Detect items без folder ("escape" detection)
curl -G -H "X-Docspell-Auth: $TOKEN" \
  --data-urlencode 'q=!exist:folder' \
  --data-urlencode 'limit=200' \
  https://docspell.medarov.net/api/v1/sec/item/search | jq '.groups[].items[] | .name'

# 3. Inbox cleanup (виж по-горе)
```

### Месечно

```bash
# 1. dsc export (secondary backup)
dsc export --all -o ~/docspell-export-$(date +%Y-%m)/

# 2. Tag growth review — има ли нови tags без category?
curl -H "X-Docspell-Auth: $TOKEN" \
  https://docspell.medarov.net/api/v1/sec/tag | \
  jq '.items[] | select(.category == null or .category == "") | .name'

# 3. Дубликат hunting (виж §19)
```

### Тримесечно

1. Re-trigger classifier training: `POST /api/v1/sec/collective/classifier/startonce`.
2. Прегледай *Collective Settings → Custom Fields*. Маркирай fields-ите без
   use.
3. Прегледай *User Settings → Sources*. Изключи неизползвани.

### Преди upgrade на Docspell

1. Full DB + files backup.
2. `dsc export --all`.
3. Прочети `Changelog.md` от мажор-до-мажор версия.
4. Snapshot на `docker-compose.yml` + `.env`.
5. Pull нова версия, `docker compose up -d`.
6. Verify: `/api/info/version` връща новата версия, login работи, item search
   връща съдържание.
7. Ако има FTS migration — `dsc admin recreate-index`.

---

## 19. Damian-specific recipes за тази седмица

Конкретен plan за следващите 7 дни. Всеки item е actionable, не теория.

### Понеделник: foundation hygiene

**1. Document Language → English.**
UI: *Collective Settings → Document Language → English*. Save.

**2. Enable TOTP 2FA.**
UI: *User Settings → Two Factor Authentication → Activate*. Сканирай QR
с Aegis. Запиши secret-а в Bitwarden. Logout + re-login да тестваш.

**3. Confirm-ни 14-те inbox items.**
Filter `inbox:yes` → отвори всеки → реши какъв е (книга / лична снимка /
DIY ръководство) → set folder + tag → Confirm.

### Вторник: sources

**4. Създай Source "scanner".**
UI: *Collective Settings → Sources → New*.
- Name: `scanner`
- Description: `ScanSnap workflow`
- Priority: `low`
- Folder: *(none — inbox)*
- Tags: създай `doctype:scan` и го прикачи

Копирай URL-а в bookmark.

**5. Създай Source "phone".**
Same UI, `phone` / *(none folder)* / tag `source:phone`. Копирай URL за
Android app-а в §12.

### Сряда-четвъртък: address book

**6. Създай Organizations за future correspondents.**

В UI: *Address Book → Organizations → New*. За всяка попълни:
- Name (български)
- Website (важно — auto-detection-ът го ползва)
- Поне един email

Списък minimum:

| Категория | Организации |
|-----------|-------------|
| Банки | ДСК Банк, UniCredit Bulbank, Postbank, Fibank, ОББ |
| Telecom | A1 България, Vivacom, Yettel |
| Utility | EVN, ЧЕЗ, Energo-Pro, Топлофикация, Софийска вода |
| Govt | НАП, НОИ |
| Couriers | Speedy, Econt |

Auto API за batch (ако ти писне ръчно):
```bash
curl -X POST -H "X-Docspell-Auth: $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "id":"", "name":"ДСК Банк", "created":0,
    "website":"https://dskbank.bg",
    "contacts":[{"id":"","kind":"email","value":"info@dskbank.bg"}],
    "use":"correspondent","notes":null,"address":null
  }' \
  https://docspell.medarov.net/api/v1/sec/organization
```

(Минимум website + email, иначе address-book rules не firing.)

### Петък: dedupe sweep

**7. Намери и изтрий дубликати.**

Списък известни дубликати от името:
- `geoeconomics.pdf` — **3** копия
- `business-cycles-and-financial-crises.pdf` — **2** копия
- `economics-of-globalization.pdf` — **2** копия
- `financial-econometrics-eviews.pdf` — **2** копия

За всеки:

```bash
# Find
curl -G -H "X-Docspell-Auth: $TOKEN" \
  --data-urlencode 'q=name:"geoeconomics"' \
  https://docspell.medarov.net/api/v1/sec/item/search | jq '.groups[].items[] | {id, name, created}'

# Decide кой да задържиш (обикновено oldest или най-голям attachment)
# Изтрий другите:
curl -X DELETE -H "X-Docspell-Auth: $TOKEN" \
  https://docspell.medarov.net/api/v1/sec/item/$ITEM_ID
```

**Гоча.** `DELETE` пуска item в **trash**, не permanent delete. Empty trash
от UI (*Items → Trash → Empty*).

### Събота: backup automation

**8. Weekly cron job.**

```bash
# /etc/cron.weekly/docspell-backup на сървъра
#!/bin/bash
set -e
DATE=$(date +%F)
BACKUP_DIR=/var/backups/docspell
mkdir -p $BACKUP_DIR

docker exec docspell-db pg_dump -U docspell docspell | gzip > $BACKUP_DIR/db-$DATE.sql.gz
docker run --rm -v docspell_files:/data -v $BACKUP_DIR:/backup alpine \
  tar czf /backup/files-$DATE.tar.gz -C /data .

# Retention — пази последните 8 седмици
find $BACKUP_DIR -name 'db-*.sql.gz' -mtime +56 -delete
find $BACKUP_DIR -name 'files-*.tar.gz' -mtime +56 -delete
```

Chmod +x. Cron-ът автоматично го пуска неделя 06:25.

### Неделя: classifier activation

**9. Trigger classifier training.**

С 678 book-ове в `Book` категория, ти си много над прага за training.

UI: *Collective Settings → Classifier Settings*.
- Enable: ✓
- Whitelist categories: `Book`, `doctype`, `area`
- Schedule: weekly Sunday 04:00

Trigger веднага:
```bash
curl -X POST -H "X-Docspell-Auth: $TOKEN" \
  https://docspell.medarov.net/api/v1/sec/collective/classifier/startonce
```

Чакаш ~3-5 мин. *Job Queue* в UI-то ще покаже `learn-classifier` task.

После: следващият upload на нова книга би трябвало да получи suggested
`Book` category tag автоматично.

---

## Заключение

700 PDF-а в Docspell без taxonomy = file dump.
700 PDF-а с правилен taxonomy + classifier + share links + 2FA =
истинска лична библиотека, която ще ти служи 10 години.

Не правиш всичко наведнъж. От plan-а отгоре, най-critical-ите за следващите
3 дни са:
1. **TOTP 2FA** (5 мин, защита).
2. **Document Language = English** (1 мин, fix-ва бъдещ OCR).
3. **Weekly backup cron** (15 мин, защита).
4. **Confirm 14-те inbox items** (~20 мин).

Останалото — sources, organizations, dedupe, classifier — е maintenance,
не emergency. Прави го разпределено през седмицата.

Documentation index: [https://docspell.org/docs/](https://docspell.org/docs/)
OpenAPI spec: [https://docspell.org/openapi/docspell-openapi.html](https://docspell.org/openapi/docspell-openapi.html)
GitHub: [https://github.com/eikek/docspell](https://github.com/eikek/docspell)
