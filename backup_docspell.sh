#!/usr/bin/env bash
#
# backup_docspell.sh — nightly backup of a Dockerized Docspell instance.
#
# Purpose
#   Produce a timestamped, self-contained backup of:
#     1. The Postgres database (pg_dump, gzip-compressed)
#     2. The Docspell file storage volume (tar.gz)
#   Plus a manifest with sizes, sha256 of the dump, and the running version.
#
# Safety
#   - Defaults to non-destructive: never deletes anything outside its own
#     BACKUP_DIR rotation policy.
#   - Reads credentials only via env vars; never echoes them.
#   - The file storage tar runs against a live volume. That is fast but is
#     NOT a fully consistent snapshot — large files in mid-write COULD be
#     captured in a torn state. For a fully consistent point-in-time
#     backup, stop the Docspell containers first (see commented block at
#     the end of this script).
#   - Exits non-zero on any failure (set -euo pipefail).
#
# Usage
#   ./backup_docspell.sh
#
# Sample crontab (run nightly at 03:00):
#   0 3 * * * /Users/dmedarov/CODING/Docspell/backup_docspell.sh >> /Users/dmedarov/Backups/Docspell/cron.log 2>&1
#
# Rotation policy
#   - Last 7 daily backups
#   - Last 4 weekly backups (those taken on Sunday)
#   - Last 12 monthly backups (those taken on the 1st of the month)
#

set -euo pipefail

# -----------------------------------------------------------------------------
# Configuration — override via environment if defaults don't fit.
# -----------------------------------------------------------------------------

: "${DOCSPELL_DB_CONTAINER:=docspell-db}"
: "${DOCSPELL_DB_USER:=docspell}"
: "${DOCSPELL_DB_NAME:=docspell}"
: "${DOCSPELL_DATA_VOLUME:=docspell_data}"
: "${BACKUP_DIR:=/Users/dmedarov/Backups/Docspell}"
: "${DOCSPELL_URL:=https://docspell.medarov.net}"

: "${KEEP_DAILY:=7}"
: "${KEEP_WEEKLY:=4}"
: "${KEEP_MONTHLY:=12}"

TIMESTAMP="$(date +%Y-%m-%dT%H-%M-%S)"
DATE_ONLY="$(date +%Y-%m-%d)"
DAY_OF_WEEK="$(date +%u)"     # 1 = Mon ... 7 = Sun
DAY_OF_MONTH="$(date +%d)"

DEST="${BACKUP_DIR}/${DATE_ONLY}_${TIMESTAMP}"
LATEST_LINK="${BACKUP_DIR}/latest"

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

log() {
    printf '[%s] %s\n' "$(date +'%Y-%m-%dT%H:%M:%S%z')" "$*"
}

die() {
    log "ERROR: $*" >&2
    exit 1
}

require() {
    command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

# Strip credentials from any text we might log (defensive — we never expect
# secrets on stdout, but if a tool ever leaks one we want it scrubbed).
redact() {
    sed -E \
        -e 's/(password=)[^[:space:]]+/\1<redacted>/Ig' \
        -e 's/(PGPASSWORD=)[^[:space:]]+/\1<redacted>/Ig' \
        -e 's/(X-Docspell-Auth:[[:space:]]*)[^[:space:]]+/\1<redacted>/Ig'
}

# -----------------------------------------------------------------------------
# Preflight
# -----------------------------------------------------------------------------

require docker
require gzip
require tar
require shasum || require sha256sum  # macOS uses shasum; Linux uses sha256sum
require curl

# Pick the sha256 tool that exists.
if command -v sha256sum >/dev/null 2>&1; then
    SHA256_CMD="sha256sum"
elif command -v shasum >/dev/null 2>&1; then
    SHA256_CMD="shasum -a 256"
else
    die "no sha256 tool (sha256sum or shasum) available"
fi

mkdir -p "${BACKUP_DIR}"
mkdir -p "${DEST}"

log "Docspell backup starting"
log "  Destination:    ${DEST}"
log "  DB container:   ${DOCSPELL_DB_CONTAINER}"
log "  DB name/user:   ${DOCSPELL_DB_NAME} / ${DOCSPELL_DB_USER}"
log "  Data volume:    ${DOCSPELL_DATA_VOLUME}"

# Verify the DB container is up.
if ! docker inspect "${DOCSPELL_DB_CONTAINER}" >/dev/null 2>&1; then
    die "DB container '${DOCSPELL_DB_CONTAINER}' not found. Is Docspell running?"
fi

# -----------------------------------------------------------------------------
# 1. pg_dump → .sql.gz
# -----------------------------------------------------------------------------

DUMP_FILE="${DEST}/docspell-db_${TIMESTAMP}.sql.gz"
log "Dumping Postgres → ${DUMP_FILE}"

# Stream pg_dump out of the container straight into gzip on the host.
# We use --no-owner / --no-privileges to make the dump portable across
# instances; remove those flags if you want exact ACL preservation.
if ! docker exec -i "${DOCSPELL_DB_CONTAINER}" \
        pg_dump \
            --username="${DOCSPELL_DB_USER}" \
            --dbname="${DOCSPELL_DB_NAME}" \
            --no-owner \
            --no-privileges \
            --format=plain \
        2> >(redact >&2) \
        | gzip -9 > "${DUMP_FILE}"; then
    die "pg_dump failed"
fi

DUMP_SIZE="$(wc -c < "${DUMP_FILE}" | tr -d ' ')"
log "  dump size: ${DUMP_SIZE} bytes"

DUMP_SHA="$(${SHA256_CMD} "${DUMP_FILE}" | awk '{print $1}')"
log "  dump sha256: ${DUMP_SHA}"

# -----------------------------------------------------------------------------
# 2. File storage volume → .tar.gz
# -----------------------------------------------------------------------------

DATA_ARCHIVE="${DEST}/docspell-data_${TIMESTAMP}.tar.gz"
log "Archiving volume '${DOCSPELL_DATA_VOLUME}' → ${DATA_ARCHIVE}"
log "  NOTE: live volume snapshot — see header for consistency caveat."

# Mount the volume into an alpine container at /data and tar it out
# directly to the host filesystem via a bind mount.
if ! docker run --rm \
        -v "${DOCSPELL_DATA_VOLUME}:/data:ro" \
        -v "${DEST}:/backup" \
        alpine:3 \
        sh -c "cd /data && tar -czf /backup/$(basename "${DATA_ARCHIVE}") ."; then
    die "volume archive failed"
fi

DATA_SIZE="$(wc -c < "${DATA_ARCHIVE}" | tr -d ' ')"
log "  archive size: ${DATA_SIZE} bytes"

# -----------------------------------------------------------------------------
# 3. Version probe + manifest
# -----------------------------------------------------------------------------

VERSION_JSON="$(curl -fsS --max-time 15 "${DOCSPELL_URL}/api/info/version" 2>/dev/null || echo '{}')"
DOCSPELL_VERSION="$(printf '%s' "${VERSION_JSON}" \
    | sed -nE 's/.*"version"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p')"
DOCSPELL_VERSION="${DOCSPELL_VERSION:-unknown}"
log "  Docspell version: ${DOCSPELL_VERSION}"

MANIFEST="${DEST}/manifest.txt"
{
    echo "docspell-backup-manifest v1"
    echo "timestamp:         ${TIMESTAMP}"
    echo "date:              ${DATE_ONLY}"
    echo "docspell_url:      ${DOCSPELL_URL}"
    echo "docspell_version:  ${DOCSPELL_VERSION}"
    echo "db_container:      ${DOCSPELL_DB_CONTAINER}"
    echo "db_user:           ${DOCSPELL_DB_USER}"
    echo "db_name:           ${DOCSPELL_DB_NAME}"
    echo "data_volume:       ${DOCSPELL_DATA_VOLUME}"
    echo ""
    echo "files:"
    echo "  $(basename "${DUMP_FILE}")"
    echo "    size:   ${DUMP_SIZE}"
    echo "    sha256: ${DUMP_SHA}"
    echo "  $(basename "${DATA_ARCHIVE}")"
    echo "    size:   ${DATA_SIZE}"
} > "${MANIFEST}"

log "Manifest written: ${MANIFEST}"

# Update 'latest' symlink for convenience (best-effort).
ln -snf "${DEST}" "${LATEST_LINK}" 2>/dev/null || true

# -----------------------------------------------------------------------------
# 4. Rotation
# -----------------------------------------------------------------------------
#
# Backup directories are named YYYY-MM-DD_*. We classify each by the
# day-of-week / day-of-month encoded in its date and apply three
# independent retention windows.
#

log "Applying rotation policy (daily=${KEEP_DAILY}, weekly=${KEEP_WEEKLY}, monthly=${KEEP_MONTHLY})"

cd "${BACKUP_DIR}"

# Collect candidate backup dirs (newest first).
mapfile -t ALL_BACKUPS < <(ls -1dt ./[0-9]*_*/ 2>/dev/null | sed 's:/$::' || true)

declare -a KEEP=()

count_daily=0
count_weekly=0
count_monthly=0

for d in "${ALL_BACKUPS[@]}"; do
    bn="$(basename "${d}")"
    # Date is the leading YYYY-MM-DD portion.
    bdate="${bn%%_*}"
    if [[ ! "${bdate}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
        continue
    fi

    # macOS `date -j` vs Linux `date -d` — try GNU first, fall back to BSD.
    if dow="$(date -d "${bdate}" +%u 2>/dev/null)"; then
        dom="$(date -d "${bdate}" +%d 2>/dev/null)"
    else
        dow="$(date -j -f "%Y-%m-%d" "${bdate}" +%u 2>/dev/null || echo "")"
        dom="$(date -j -f "%Y-%m-%d" "${bdate}" +%d 2>/dev/null || echo "")"
    fi

    keep_this=0

    if (( count_daily < KEEP_DAILY )); then
        keep_this=1
        count_daily=$((count_daily + 1))
    fi
    if [[ "${dow}" == "7" ]] && (( count_weekly < KEEP_WEEKLY )); then
        keep_this=1
        count_weekly=$((count_weekly + 1))
    fi
    if [[ "${dom}" == "01" ]] && (( count_monthly < KEEP_MONTHLY )); then
        keep_this=1
        count_monthly=$((count_monthly + 1))
    fi

    if (( keep_this == 1 )); then
        KEEP+=("${bn}")
    fi
done

# Delete anything not in KEEP.
for d in "${ALL_BACKUPS[@]}"; do
    bn="$(basename "${d}")"
    skip=0
    for k in "${KEEP[@]:-}"; do
        if [[ "${bn}" == "${k}" ]]; then
            skip=1
            break
        fi
    done
    if (( skip == 0 )); then
        log "  rotating out: ${bn}"
        rm -rf -- "${BACKUP_DIR}/${bn}"
    fi
done

log "Backup complete: ${DEST}"

# -----------------------------------------------------------------------------
# Optional: fully consistent snapshot (manual variant)
# -----------------------------------------------------------------------------
#
# For a torn-write-free archive of the file storage volume, stop the
# Docspell containers first. Example using docker compose:
#
#   docker compose -f /path/to/docspell/docker-compose.yml stop docspell-joex docspell-restserver
#   ./backup_docspell.sh
#   docker compose -f /path/to/docspell/docker-compose.yml start docspell-restserver docspell-joex
#
# This adds ~30s–2min of downtime depending on instance size.
#
