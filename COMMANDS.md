# Docspell pipeline — quick command reference

Self-contained command sheet for starting a new session on a fresh Mac
(or after a long break). Assumes Tailscale is up + this repo is cloned
to `~/CODING/Docspell`.

## Table of contents

- [0. One-shot prerequisites](#0-one-shot-prerequisites-only-on-a-new-machine)
- [1. Daily verification](#1-daily-verification--health-check)
- [2. Full pipeline from scratch](#2-full-pipeline-from-scratch-rare)
- [3. Apply scripts](#3-apply-scripts-individual)
- [4. Email ingestion](#4-email-ingestion-setup-or-update)
- [5. joex OCR override (bul+eng+rus)](#5-joex-ocr-override-bulengrus)
- [6. Server-side maintenance](#6-server-side-maintenance)
- [7. Backups](#7-backups)
- [8. Overrides cheatsheet](#8-overrides-cheatsheet)
- [9. Common troubleshooting](#9-common-troubleshooting)

---

## 0. One-shot prerequisites (only on a new machine)

### Tailscale
```bash
# Ensure Tailscale is up and pve is reachable
ssh root@pve 'hostname'   # → pve
```
If "Could not resolve hostname pve" → check Tailscale, then:
```bash
# Add a fallback host entry only if Tailscale's MagicDNS is unstable
echo '100.66.18.7 docspell.medarov.net' | sudo tee -a /etc/hosts
```

### zsh comment support (so you can paste examples with `#`)
```bash
echo 'setopt interactive_comments' >> ~/.zshrc && source ~/.zshrc
```

### Store credentials in macOS keychain (one-time)
```bash
# Docspell login password
security add-generic-password -s docspell -a library/dmedarov -w
# (prompts twice — paste password, retype)

# Gmail App Password (generate at https://myaccount.google.com/apppasswords)
security add-generic-password -s docspell-gmail -a damian.medarov@gmail.com -w
# (prompts twice — paste 16-char App Password, retype)

# verify both stored
security find-generic-password -s docspell        -a library/dmedarov     -w | wc -c   # should be > 1
security find-generic-password -s docspell-gmail  -a damian.medarov@gmail.com -w | wc -c # = 17 (16 + newline)
```

---

## 1. Daily verification / health check

```bash
cd ~/CODING/Docspell

# Read-only, full check
python3 verify_docspell.py

# Brief mode (cron-friendly)
python3 verify_docspell.py --brief

# Custom URL/account (rare)
python3 verify_docspell.py --url https://docspell.medarov.net --account library/dmedarov --brief
```
Expected: 8/9 OK with 0–3 cosmetic anomalies (see SCHEMA.md).

---

## 2. Full pipeline from scratch (rare)

For a completely fresh Docspell instance with similar dataset (e.g.,
re-doing the library on a new server). All scripts default to **dry-run**;
add `--apply --confirm <PHRASE>` to commit.

```bash
cd ~/CODING/Docspell

# Step 1: classify the inbox by file name (offline, fast)
python3 classify_by_name.py \
  --inbox-csv /path/to/inbox.csv \
  --out out/docspell-name-classification.csv \
  --stats

# Step 2: review the CSV, then fix-up the schema (Archive → Library, area:X → Book:X)
python3 fix_csv_schema.py \
  --in  out/docspell-name-classification.csv \
  --out out/docspell-name-classification-fixed.csv

# Step 3: apply folder + tags (idempotent)
python3 apply_reviewed_actions.py \
  --csv out/docspell-name-classification-fixed.csv \
  --apply --confirm APPLY-LIBRARY

# Step 4: seed Bulgarian correspondents (banks, telecom, utility, gov, etc.)
python3 seed_organizations.py \
  --apply --confirm SEED-ORGS

# Step 5: dedupe by title hash (interactive 2nd prompt for safety)
python3 dedupe_items.py \
  --apply --confirm DEDUPE-DELETE

# Step 6: online enrichment via OpenLibrary + Google Books
#         (requires GOOGLE_BOOKS_API_KEY in env)
export GOOGLE_BOOKS_API_KEY="<your-key>"
python3 docspell_book_system_enriched/docspell_book_classifier.py \
  --online-enrich \
  --output out/books-enriched/book-enrichment.csv

# Step 7: apply enrichment as custom fields (year, isbn, author, publisher, source)
python3 apply_book_enrichment.py \
  --csv out/books-enriched/book-enrichment.csv \
  --apply --confirm APPLY-ENRICHMENT

# Step 8: setup email ingestion (Gmail → Docspell)
python3 setup_email_ingestion.py \
  --gmail damian.medarov@gmail.com \
  --start-once \
  --apply --confirm SETUP-EMAIL
```

Or do everything via the orchestrator:
```bash
./run_full_pipeline.sh \
  --apply \
  --password-file <(security find-generic-password -s docspell -a library/dmedarov -w)
```

---

## 3. Apply scripts (individual)

Each is idempotent — re-running is safe.

### Apply folder + tags
```bash
python3 apply_reviewed_actions.py \
  --csv out/docspell-name-classification-fixed.csv \
  --apply --confirm APPLY-LIBRARY \
  [--url https://docspell.medarov.net] \
  [--account library/dmedarov]
```

### Seed organizations
```bash
python3 seed_organizations.py \
  --apply --confirm SEED-ORGS
```

### Apply book enrichment (custom fields)
```bash
python3 apply_book_enrichment.py \
  --csv out/books-enriched/book-enrichment.csv \
  --apply --confirm APPLY-ENRICHMENT \
  [--overwrite]   # re-set fields even if already set
```

### Dedupe items
```bash
# Dry-run shows what would be deleted
python3 dedupe_items.py

# Apply — TWO confirmations required
python3 dedupe_items.py --apply --confirm DEDUPE-DELETE
# (then type "yes" at the interactive prompt)
```

### CSV schema migration
```bash
python3 fix_csv_schema.py \
  --in  out/docspell-name-classification.csv \
  --out out/docspell-name-classification-fixed.csv
```

---

## 4. Email ingestion — setup or update

### First-time setup
```bash
# Prerequisites done in section 0:
#   - Gmail App Password in keychain (service: docspell-gmail)
#   - Gmail label "Docspell" + filter to:*+docspell@gmail.com created in browser

python3 setup_email_ingestion.py \
  --gmail damian.medarov@gmail.com \
  --apply --confirm SETUP-EMAIL
```

### With overrides
```bash
python3 setup_email_ingestion.py \
  --gmail damian.medarov@gmail.com \
  --imap-name gmail-docspell \
  --label Docspell \
  --folder-name Library \
  --schedule '*-*-* *:0/15:00 UTC' \
  --received-since-hours 168 \
  --attachments-only true \
  --start-once \
  --apply --confirm SETUP-EMAIL
```

### Trigger scan manually (without waiting 15 min)
```bash
# Get fresh token
PW=$(security find-generic-password -s docspell -a library/dmedarov -w)
TOKEN=$(curl -s -X POST -H 'Content-Type: application/json' \
  -d "{\"account\":\"library/dmedarov\",\"password\":\"$PW\"}" \
  https://docspell.medarov.net/api/v1/open/auth/login | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
unset PW

# Get task id + body
curl -s -H "X-Docspell-Auth: $TOKEN" \
  https://docspell.medarov.net/api/v1/sec/usertask/scanmailbox \
  | tee /tmp/scan.json

# Trigger immediate scan (pass full body of first task)
python3 -c "import json; d=json.load(open('/tmp/scan.json')); print(json.dumps(d['items'][0]))" \
  > /tmp/scan-body.json
curl -s -X POST -H "X-Docspell-Auth: $TOKEN" -H 'Content-Type: application/json' \
  --data @/tmp/scan-body.json \
  https://docspell.medarov.net/api/v1/sec/usertask/scanmailbox/startonce
```

### Check queue state
```bash
curl -s -H "X-Docspell-Auth: $TOKEN" \
  https://docspell.medarov.net/api/v1/sec/queue/state \
  | python3 -m json.tool | head -50
```

### Update IMAP password (after Gmail App Password rotation)
```bash
# Rotate keychain
security delete-generic-password -s docspell-gmail
security add-generic-password -s docspell-gmail -a damian.medarov@gmail.com -w

# Re-run setup script — it does upsert (PUT if exists)
python3 setup_email_ingestion.py \
  --gmail damian.medarov@gmail.com \
  --apply --confirm SETUP-EMAIL
```

---

## 5. joex OCR override (bul+eng+rus)

### Verify override is active
```bash
# 1) Confirm the mounted file matches our repo version (md5)
ssh root@pve 'pct exec 104 -- docker exec docspell-joex md5sum \
  /opt/docspell-joex/conf/docspell-joex.conf'
# Compare with: md5 -q ~/CODING/Docspell/conf/joex-override.conf

# 2) Confirm JVM sees the config file
ssh root@pve 'pct exec 104 -- docker exec docspell-joex bash -c \
  "ps -ef | grep java | grep -v grep"' | tr ' ' '\n' | grep config.file
# Expected: -Dconfig.file=/opt/docspell-joex/bin/../conf/docspell-joex.conf

# 3) Confirm joex is on PostgreSQL (not H2 fallback)
ssh root@pve 'pct exec 104 -- docker logs --tail 100 docspell-joex 2>&1' \
  | grep -i 'Database:' | tail -3
# Expected: jdbc:postgresql://db:5432/dbname

# 4) Count actual override args lines (excluding comments)
ssh root@pve 'pct exec 104 -- docker exec docspell-joex bash -c \
  "grep -v ^# /opt/docspell-joex/conf/docspell-joex.conf | grep -c bul+eng+rus"'
# Expected: 3
```

### Re-deploy override (after editing conf/joex-override.conf)
```bash
# Push updated config
scp conf/joex-override.conf root@pve:/tmp/joex-override.conf
ssh root@pve 'pct push 104 /tmp/joex-override.conf \
  /root/docspell-docker/docker-compose/conf/docspell-joex.conf'

# Recreate joex container
ssh root@pve "pct exec 104 -- bash -c \
  'cd /root/docspell-docker/docker-compose && docker compose up -d joex'"

# Verify (wait 10s for startup)
sleep 10
ssh root@pve 'pct exec 104 -- docker logs --tail 20 docspell-joex 2>&1 | grep -iE "Database:"'
# Expected: PostgreSQL (NOT H2 demo)
```

### Revert override
```bash
ssh root@pve 'pct exec 104 -- python3 -c "
path = \"/root/docspell-docker/docker-compose/docker-compose.yml\"
with open(path) as f: t = f.read()
old = \"\"\"    volumes:
      - ./conf/docspell-joex.conf:/opt/docspell-joex/conf/docspell-joex.conf:ro
\"\"\"
if old in t:
    with open(path,\"w\") as f: f.write(t.replace(old,\"\",1))
    print(\"reverted\")
else:
    print(\"already reverted\")
"'
ssh root@pve "pct exec 104 -- bash -c \
  'cd /root/docspell-docker/docker-compose && docker compose up -d joex'"
```

---

## 6. Server-side maintenance

### See running containers
```bash
ssh root@pve 'pct exec 104 -- docker ps'
```

### Tail joex logs (real-time OCR command observation)
```bash
ssh root@pve 'pct exec 104 -- docker logs -f docspell-joex 2>&1' | grep -iE 'ocrmypdf|tesseract|Job execution'
```

### Restart joex (clean)
```bash
ssh root@pve "pct exec 104 -- bash -c \
  'cd /root/docspell-docker/docker-compose && docker compose restart joex'"
```

### Restart all Docspell services
```bash
ssh root@pve "pct exec 104 -- bash -c \
  'cd /root/docspell-docker/docker-compose && docker compose restart'"
```

### Change TZ (Europe/Sofia / Europe/Berlin / UTC etc.)
```bash
ssh root@pve "pct exec 104 -- bash -c 'cd /root/docspell-docker/docker-compose && \
  sed -i \"s|TZ: .Europe/Berlin.|TZ: \\\"Europe/Sofia\\\"|g\" docker-compose.yml && \
  docker compose up -d'"
```

### Inspect PostgreSQL data sizes
```bash
ssh root@pve 'pct exec 104 -- docker exec postgres_db psql -U dbuser -d dbname \
  -c "SELECT pg_size_pretty(pg_database_size(\"dbname\")) AS db_size;"'
```

---

## 7. Backups

### dsc (Docspell user export — recommended)
```bash
# install once on Mac
brew install dsc

# weekly snapshot to your local disk
DOCSPELL_BACKUP_MODE=dsc ./backup_docspell.sh

# or manually
dsc -d https://docspell.medarov.net \
    -u library/dmedarov \
    export --target ~/Backups/docspell/$(date +%Y-%m-%d) --all
```

### ssh (server-side pg_dump + volume tar)
```bash
DOCSPELL_BACKUP_MODE=ssh DOCSPELL_SSH_HOST=pve ./backup_docspell.sh
# → produces ~/Backups/docspell/<date>/db.sql.gz + data.tar.gz
```

### local (if you run docker on the Mac itself — not our setup)
```bash
DOCSPELL_BACKUP_MODE=local ./backup_docspell.sh
```

---

## 8. Overrides cheatsheet

### Common script flags (apply across all *.py)
| Flag | Default | Purpose |
| --- | --- | --- |
| `--url` | `https://docspell.medarov.net` | Docspell base URL |
| `--account` | `library/dmedarov` | Collective/user login |
| `--dry-run` | implicit | Show plan, don't write |
| `--apply` | required for writes | Actually apply changes |
| `--confirm <PHRASE>` | required with `--apply` | Per-script phrase (see table) |

### Confirmation phrases
| Script | Phrase |
| --- | --- |
| `apply_reviewed_actions.py` | `APPLY-LIBRARY` |
| `apply_book_enrichment.py` | `APPLY-ENRICHMENT` |
| `seed_organizations.py` | `SEED-ORGS` |
| `dedupe_items.py` | `DEDUPE-DELETE` (+ interactive `yes`) |
| `setup_email_ingestion.py` | `SETUP-EMAIL` |

### Environment overrides
```bash
# Use a different Docspell instance for testing
export DOCSPELL_URL=https://staging.docspell.example.com
export DOCSPELL_ACCOUNT=test/user
python3 verify_docspell.py --url "$DOCSPELL_URL" --account "$DOCSPELL_ACCOUNT"

# Use a different keychain entry
python3 setup_email_ingestion.py \
  --keychain-service docspell-staging \
  --gmail-keychain-service docspell-gmail-staging \
  --gmail other@gmail.com \
  --apply --confirm SETUP-EMAIL
```

### Pipeline orchestrator (run_full_pipeline.sh)
```bash
./run_full_pipeline.sh \
  --dry-run                       # show all phases without writing
  --phases 1,2,3                  # run only specific phases
  --password-file <(security find-generic-password -s docspell -a library/dmedarov -w)
  --skip-enrichment               # if you don't have GOOGLE_BOOKS_API_KEY
  --skip-dedupe                   # safer first pass
```

---

## 8.5. Auto-tag classifier (Docspell native ML)

Docspell trains an n-gram classifier from your existing labelled items
and uses it to suggest tags + correspondent + persons + equipment for
new items entering the system. Configured for this collective with:

- categoryList: `["Book"]` (only Book:* tags are predicted)
- listType: `whitelist`
- itemCount: 300 (newest 300 items used as training data)
- schedule: `*-*-* 03:30:00 UTC` (daily retrain at 03:30 UTC)

### Trigger an immediate retrain
```bash
PW=$(security find-generic-password -s docspell -a library/dmedarov -w)
TOKEN=$(curl -s -X POST -H 'Content-Type: application/json' \
  -d "{\"account\":\"library/dmedarov\",\"password\":\"$PW\"}" \
  https://docspell.medarov.net/api/v1/open/auth/login \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
unset PW
curl -s -X POST -H "X-Docspell-Auth: $TOKEN" \
  https://docspell.medarov.net/api/v1/sec/collective/classifier/startonce
# {"success":true,"message":"Job submitted."}
```

### Watch training progress
```bash
curl -s -H "X-Docspell-Auth: $TOKEN" \
  https://docspell.medarov.net/api/v1/sec/queue/state \
  | python3 -m json.tool | grep -A 2 -i "Learn"
```

### Change config
```bash
curl -s -H "X-Docspell-Auth: $TOKEN" \
  https://docspell.medarov.net/api/v1/sec/collective/settings > /tmp/cs.json

# Edit /tmp/cs.json — adjust classifier block
python3 -c "
import json
d = json.load(open('/tmp/cs.json'))
d['classifier']['categoryList'] = ['Book', 'doctype']  # add doctype later
d['classifier']['itemCount'] = 500
print(json.dumps(d, indent=2))" > /tmp/cs-new.json

curl -s -X POST -H "X-Docspell-Auth: $TOKEN" -H 'Content-Type: application/json' \
  --data @/tmp/cs-new.json \
  https://docspell.medarov.net/api/v1/sec/collective/settings
```

### Disable classifier
Set `categoryList: []` — the daily job will skip training, no auto-tagging.

---

## 9. Common troubleshooting

### "Authentication failed" mid-script
Token TTL is ~5 min. Re-login:
```bash
PW=$(security find-generic-password -s docspell -a library/dmedarov -w)
export TOKEN=$(curl -s -X POST -H 'Content-Type: application/json' \
  -d "{\"account\":\"library/dmedarov\",\"password\":\"$PW\"}" \
  https://docspell.medarov.net/api/v1/open/auth/login \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
unset PW
echo "token len=${#TOKEN}"
```

### Could not resolve docspell.medarov.net
Tailscale is down or MagicDNS is broken.
```bash
# Check Tailscale
tailscale status

# Fallback to /etc/hosts (one-time)
echo '100.66.18.7 docspell.medarov.net' | sudo tee -a /etc/hosts
```

### joex isn't processing jobs (worker=null in queue)
Likely OCR override misconfig caused fallback to H2.
```bash
ssh root@pve 'pct exec 104 -- docker logs --tail 30 docspell-joex 2>&1 | grep -i "Database:"'
# If it says h2 → override config broke env-var bindings → revert (see §5).
```

### OCR returns garbled Bulgarian (mojibake like ÎÂÌËÂ)
Override config is NOT loaded.
```bash
# verify — count non-comment args lines containing bul+eng+rus
ssh root@pve 'pct exec 104 -- docker exec docspell-joex bash -c \
  "grep -v ^# /opt/docspell-joex/conf/docspell-joex.conf | grep -c bul+eng+rus"'
# Expected: 3 (one per command override: extraction.ocr, convert.tesseract, convert.ocrmypdf)
#
# Note: a plain `grep -c` (without the comment filter) returns 5 — that's
# 3 actual config lines + 2 documentation comment lines in the conf file.
# If you see 0 → mount missing or config not deployed; see §5.
```

### Email scan finds 0 mails but Gmail label has messages
Subject filter mismatch.
```bash
# Pull current config — if subjectFilter is not null, that's the bug
curl -s -H "X-Docspell-Auth: $TOKEN" \
  https://docspell.medarov.net/api/v1/sec/usertask/scanmailbox \
  | python3 -m json.tool

# Re-run setup script — it omits empty filter fields
python3 setup_email_ingestion.py --gmail damian.medarov@gmail.com \
  --apply --confirm SETUP-EMAIL
```

### Docspell UI shows wrong time
Container TZ is misconfigured. See §6 "Change TZ".

---

## Known broken in v0.43.0 — SMTP / "Send Item via E-Mail"

**Bug**: POST `/sec/email/settings/smtp` returns
`Internal error: ERROR: INSERT has more expressions than target columns`.
Upstream issue [eikek/docspell#3099](https://github.com/eikek/docspell/issues/3099)
(open since 2025-07-05, confirmed by maintainer).

**Diagnostic** (if you forget and try again):
```bash
# This will fail; preserved here so you don't waste time debugging it again
PW=$(security find-generic-password -s docspell -a library/dmedarov -w)
TOKEN=$(curl -s -X POST -H 'Content-Type: application/json' \
  -d "{\"account\":\"library/dmedarov\",\"password\":\"$PW\"}" \
  https://docspell.medarov.net/api/v1/open/auth/login \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
unset PW

curl -s -X POST -H "X-Docspell-Auth: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"smtp-test","smtpHost":"smtp.gmail.com","smtpPort":587,"smtpUser":"x@y.z","smtpPassword":"p","from":"x@y.z","sslType":"starttls","ignoreCertificates":false}' \
  https://docspell.medarov.net/api/v1/sec/email/settings/smtp
# {"success":false,"message":"Internal error: ERROR: INSERT has more expressions than target columns ..."}
```

**Workaround**: skip the feature. To "send item via email":
- Open the item in Docspell UI
- Download the attachment(s) locally (toolbar → download)
- Compose a new email in Gmail web/desktop, attach the file
- Send manually

**If upstream fix lands** (watch the issue): upgrade Docspell joex+restserver
images to the version that includes the patch, then SMTP works via the
normal UI/API. No code changes needed on our side.

## Quick reference: production state (as of 2026-05-20)

- Docspell version: **0.43.0**
- Collective: `library`, user `dmedarov`
- Items: **670 organized** (654 Library, 2 Personal, ~14 inbox)
- Tags: **21 Book:* + 5 categoryless preserved**
- Custom fields: **5** (book_year, book_isbn, book_publisher, book_author, book_source)
- Organizations: **30 seeded** + 1 user-existing
- OCR: **multi-language (bul+eng+rus)** via `conf/joex-override.conf` mount
- Email ingestion: **Gmail "Docspell" label → Library folder** every 15 min via `gmail-docspell` IMAP connection + `FUzMr5bqKZt-...` scanmailbox task
- TZ: **Europe/Sofia**
- GitHub: https://github.com/dmedarov/Docspell
