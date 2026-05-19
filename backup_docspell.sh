#!/bin/bash
#
# backup_docspell.sh — nightly backup of a Docspell instance.
#
# Three modes (set DOCSPELL_BACKUP_MODE explicitly, or let auto-detect choose):
#
#   dsc    Run `dsc export` entirely on the local Mac. No Docker required.
#          Produces a user-portable archive (items + metadata) via the
#          Docspell CLI. This is the recommended mode for Damian's setup
#          (server is on a remote Proxmox VE host, only Tailscale-reachable).
#
#   ssh    Run docker/pg_dump on a remote host over SSH, then scp the dump
#          back to ${BACKUP_DIR}. Volume tar is also produced remotely and
#          fetched. Requires SSH access to ${DOCSPELL_SSH_HOST:-pve}.
#
#   local  Original behavior — Docker is on the same machine. Runs
#          `docker exec ... pg_dump ...` against a local container and
#          tars a local docker volume. Kept for users who self-host on
#          the same box as this script.
#
# Auto-detect order when DOCSPELL_BACKUP_MODE is unset:
#   1) dsc   (if `command -v dsc` succeeds)
#   2) ssh   (if DOCSPELL_SSH_HOST is set OR `ssh -G pve` resolves a host)
#   3) local (fallback)
#
# Rotation policy (per mode — separate subdirs under BACKUP_DIR):
#   - Last 7 daily backups
#   - Last 4 weekly backups (Sundays)
#   - Last 12 monthly backups (1st of month)
#
# Usage
#   ./backup_docspell.sh
#   DOCSPELL_BACKUP_MODE=ssh DOCSPELL_SSH_HOST=pve ./backup_docspell.sh
#   DOCSPELL_BACKUP_MODE=dsc ./backup_docspell.sh
#
# Sample crontab (nightly at 03:00):
#   0 3 * * * /Users/dmedarov/CODING/Docspell/backup_docspell.sh \
#       >> /Users/dmedarov/Backups/Docspell/cron.log 2>&1
#

set -euo pipefail

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

: "${DOCSPELL_DB_CONTAINER:=docspell-db}"
: "${DOCSPELL_DB_USER:=docspell}"
: "${DOCSPELL_DB_NAME:=docspell}"
: "${DOCSPELL_DATA_VOLUME:=docspell_data}"
: "${BACKUP_DIR:=/Users/dmedarov/Backups/Docspell}"
: "${DOCSPELL_URL:=https://docspell.medarov.net}"
: "${DOCSPELL_SSH_HOST:=}"      # e.g. "pve" or "user@pve"
: "${DOCSPELL_BACKUP_MODE:=}"   # one of: dsc, ssh, local

: "${KEEP_DAILY:=7}"
: "${KEEP_WEEKLY:=4}"
: "${KEEP_MONTHLY:=12}"

TIMESTAMP="$(date +%Y-%m-%dT%H-%M-%S)"
DATE_ONLY="$(date +%Y-%m-%d)"

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

# Strip credentials from any text we might log defensively.
redact() {
    sed -E \
        -e 's/(password=)[^[:space:]]+/\1<redacted>/Ig' \
        -e 's/(PGPASSWORD=)[^[:space:]]+/\1<redacted>/Ig' \
        -e 's/(X-Docspell-Auth:[[:space:]]*)[^[:space:]]+/\1<redacted>/Ig'
}

# -----------------------------------------------------------------------------
# Pick the sha256 tool that exists (macOS vs Linux).
# -----------------------------------------------------------------------------

if command -v sha256sum >/dev/null 2>&1; then
    SHA256_CMD="sha256sum"
elif command -v shasum >/dev/null 2>&1; then
    SHA256_CMD="shasum -a 256"
else
    die "no sha256 tool (sha256sum or shasum) available"
fi

# -----------------------------------------------------------------------------
# Mode auto-detection
# -----------------------------------------------------------------------------

detect_mode() {
    if [ -n "${DOCSPELL_BACKUP_MODE}" ]; then
        printf '%s' "${DOCSPELL_BACKUP_MODE}"
        return
    fi
    if command -v dsc >/dev/null 2>&1; then
        printf '%s' "dsc"
        return
    fi
    if [ -n "${DOCSPELL_SSH_HOST}" ] || ssh -G pve >/dev/null 2>&1; then
        printf '%s' "ssh"
        return
    fi
    printf '%s' "local"
}

MODE="$(detect_mode)"
case "${MODE}" in
    dsc|ssh|local) : ;;
    *) die "unknown DOCSPELL_BACKUP_MODE='${MODE}' (expected: dsc, ssh, local)" ;;
esac

# Per-mode destination (separate rotation pools).
MODE_DIR="${BACKUP_DIR}/${MODE}"
DEST="${MODE_DIR}/${DATE_ONLY}_${TIMESTAMP}"
LATEST_LINK="${MODE_DIR}/latest"

mkdir -p "${MODE_DIR}" "${DEST}"

log "Docspell backup starting"
log "  Mode:           ${MODE}"
log "  Destination:    ${DEST}"
log "  Docspell URL:   ${DOCSPELL_URL}"

# -----------------------------------------------------------------------------
# Version probe (best-effort, used by all modes for the manifest)
# -----------------------------------------------------------------------------

probe_version() {
    if ! command -v curl >/dev/null 2>&1; then
        printf '%s' "unknown"
        return
    fi
    local json
    json="$(curl -fsS --max-time 15 "${DOCSPELL_URL}/api/info/version" 2>/dev/null || echo '{}')"
    local v
    v="$(printf '%s' "${json}" \
        | sed -nE 's/.*"version"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p')"
    printf '%s' "${v:-unknown}"
}

DOCSPELL_VERSION="$(probe_version)"
log "  Docspell version: ${DOCSPELL_VERSION}"

# -----------------------------------------------------------------------------
# Mode: dsc — run the Docspell CLI entirely locally.
# -----------------------------------------------------------------------------

backup_dsc() {
    require dsc

    local export_target="${DEST}/dsc-export"
    mkdir -p "${export_target}"

    log "Running dsc export → ${export_target}"
    if ! dsc export --target "${export_target}" 2> >(redact >&2); then
        die "dsc export failed"
    fi

    # Roll the exported tree into a single tar.gz for sane storage + checksum.
    local archive="${DEST}/docspell-dsc_${TIMESTAMP}.tar.gz"
    log "Compressing export → ${archive}"
    tar -C "${DEST}" -czf "${archive}" "dsc-export"
    rm -rf "${export_target}"

    local size sha
    size="$(wc -c < "${archive}" | tr -d ' ')"
    sha="$(${SHA256_CMD} "${archive}" | awk '{print $1}')"
    log "  archive size: ${size} bytes"
    log "  archive sha256: ${sha}"

    {
        echo "docspell-backup-manifest v1"
        echo "mode:              dsc"
        echo "timestamp:         ${TIMESTAMP}"
        echo "date:              ${DATE_ONLY}"
        echo "docspell_url:      ${DOCSPELL_URL}"
        echo "docspell_version:  ${DOCSPELL_VERSION}"
        echo ""
        echo "files:"
        echo "  $(basename "${archive}")"
        echo "    size:   ${size}"
        echo "    sha256: ${sha}"
    } > "${DEST}/manifest.txt"
}

# -----------------------------------------------------------------------------
# Mode: ssh — run docker/pg_dump remotely, fetch via scp.
# -----------------------------------------------------------------------------

backup_ssh() {
    require ssh
    require scp
    require gzip

    local host="${DOCSPELL_SSH_HOST:-pve}"
    log "  SSH host: ${host}"

    # Verify SSH reachability and the DB container exists remotely.
    if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "${host}" "true" 2>/dev/null; then
        die "ssh to '${host}' failed (BatchMode). Check Tailscale + key auth."
    fi

    if ! ssh "${host}" "docker inspect ${DOCSPELL_DB_CONTAINER}" >/dev/null 2>&1; then
        die "DB container '${DOCSPELL_DB_CONTAINER}' not found on ${host}"
    fi

    local dump_file="${DEST}/docspell-db_${TIMESTAMP}.sql.gz"
    log "Dumping Postgres on ${host} → ${dump_file}"

    # Stream pg_dump from remote container directly to local gzip-compressed file.
    if ! ssh "${host}" \
            "docker exec -i ${DOCSPELL_DB_CONTAINER} \
                pg_dump \
                    --username=${DOCSPELL_DB_USER} \
                    --dbname=${DOCSPELL_DB_NAME} \
                    --no-owner \
                    --no-privileges \
                    --format=plain" \
            2> >(redact >&2) \
            | gzip -9 > "${dump_file}"; then
        die "remote pg_dump failed"
    fi

    local dump_size dump_sha
    dump_size="$(wc -c < "${dump_file}" | tr -d ' ')"
    dump_sha="$(${SHA256_CMD} "${dump_file}" | awk '{print $1}')"
    log "  dump size: ${dump_size} bytes"
    log "  dump sha256: ${dump_sha}"

    # Volume tar: produce on remote in /tmp, scp back, then delete remote copy.
    local remote_archive="/tmp/docspell-data_${TIMESTAMP}.tar.gz"
    local data_archive="${DEST}/docspell-data_${TIMESTAMP}.tar.gz"
    log "Archiving remote volume '${DOCSPELL_DATA_VOLUME}' → ${data_archive}"

    if ! ssh "${host}" \
            "docker run --rm \
                -v ${DOCSPELL_DATA_VOLUME}:/data:ro \
                -v /tmp:/backup \
                alpine:3 \
                sh -c 'cd /data && tar -czf /backup/$(basename "${remote_archive}") .'"; then
        die "remote volume archive failed"
    fi

    if ! scp -q "${host}:${remote_archive}" "${data_archive}"; then
        die "scp of volume archive failed"
    fi
    ssh "${host}" "rm -f ${remote_archive}" || true

    local data_size
    data_size="$(wc -c < "${data_archive}" | tr -d ' ')"
    log "  archive size: ${data_size} bytes"

    {
        echo "docspell-backup-manifest v1"
        echo "mode:              ssh"
        echo "ssh_host:          ${host}"
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
        echo "  $(basename "${dump_file}")"
        echo "    size:   ${dump_size}"
        echo "    sha256: ${dump_sha}"
        echo "  $(basename "${data_archive}")"
        echo "    size:   ${data_size}"
    } > "${DEST}/manifest.txt"
}

# -----------------------------------------------------------------------------
# Mode: local — original Docker-on-this-machine behavior.
# -----------------------------------------------------------------------------

backup_local() {
    require docker
    require gzip
    require tar

    if ! docker inspect "${DOCSPELL_DB_CONTAINER}" >/dev/null 2>&1; then
        die "DB container '${DOCSPELL_DB_CONTAINER}' not found. Is Docspell running?"
    fi

    local dump_file="${DEST}/docspell-db_${TIMESTAMP}.sql.gz"
    log "Dumping Postgres → ${dump_file}"
    if ! docker exec -i "${DOCSPELL_DB_CONTAINER}" \
            pg_dump \
                --username="${DOCSPELL_DB_USER}" \
                --dbname="${DOCSPELL_DB_NAME}" \
                --no-owner \
                --no-privileges \
                --format=plain \
            2> >(redact >&2) \
            | gzip -9 > "${dump_file}"; then
        die "pg_dump failed"
    fi

    local dump_size dump_sha
    dump_size="$(wc -c < "${dump_file}" | tr -d ' ')"
    dump_sha="$(${SHA256_CMD} "${dump_file}" | awk '{print $1}')"
    log "  dump size: ${dump_size} bytes"
    log "  dump sha256: ${dump_sha}"

    local data_archive="${DEST}/docspell-data_${TIMESTAMP}.tar.gz"
    log "Archiving volume '${DOCSPELL_DATA_VOLUME}' → ${data_archive}"
    log "  NOTE: live volume snapshot — see header for consistency caveat."
    if ! docker run --rm \
            -v "${DOCSPELL_DATA_VOLUME}:/data:ro" \
            -v "${DEST}:/backup" \
            alpine:3 \
            sh -c "cd /data && tar -czf /backup/$(basename "${data_archive}") ."; then
        die "volume archive failed"
    fi

    local data_size
    data_size="$(wc -c < "${data_archive}" | tr -d ' ')"
    log "  archive size: ${data_size} bytes"

    {
        echo "docspell-backup-manifest v1"
        echo "mode:              local"
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
        echo "  $(basename "${dump_file}")"
        echo "    size:   ${dump_size}"
        echo "    sha256: ${dump_sha}"
        echo "  $(basename "${data_archive}")"
        echo "    size:   ${data_size}"
    } > "${DEST}/manifest.txt"
}

# -----------------------------------------------------------------------------
# Run the chosen mode.
# -----------------------------------------------------------------------------

case "${MODE}" in
    dsc)   backup_dsc   ;;
    ssh)   backup_ssh   ;;
    local) backup_local ;;
esac

log "Manifest written: ${DEST}/manifest.txt"

# Update per-mode 'latest' symlink (best-effort).
ln -snf "${DEST}" "${LATEST_LINK}" 2>/dev/null || true

# -----------------------------------------------------------------------------
# Rotation — per mode (each mode rotates within its own subdir).
# -----------------------------------------------------------------------------

log "Applying rotation policy (daily=${KEEP_DAILY}, weekly=${KEEP_WEEKLY}, monthly=${KEEP_MONTHLY})"

cd "${MODE_DIR}"

# Collect candidate backup dirs (newest first).
ALL_BACKUPS=()
while IFS= read -r line; do
    ALL_BACKUPS+=("${line%/}")
done < <(ls -1dt ./[0-9]*_*/ 2>/dev/null || true)

KEEP=()
count_daily=0
count_weekly=0
count_monthly=0

for d in "${ALL_BACKUPS[@]:-}"; do
    [ -z "${d}" ] && continue
    bn="$(basename "${d}")"
    bdate="${bn%%_*}"
    if [[ ! "${bdate}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
        continue
    fi

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

for d in "${ALL_BACKUPS[@]:-}"; do
    [ -z "${d}" ] && continue
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
        rm -rf -- "${MODE_DIR}/${bn}"
    fi
done

log "Backup complete: ${DEST}"

# -----------------------------------------------------------------------------
# Optional: fully consistent snapshot (manual variant) — local/ssh modes only.
# -----------------------------------------------------------------------------
#
# For a torn-write-free archive of the file storage volume, stop the
# Docspell containers first. Example using docker compose:
#
#   docker compose -f /path/to/docspell/docker-compose.yml \
#       stop docspell-joex docspell-restserver
#   ./backup_docspell.sh
#   docker compose -f /path/to/docspell/docker-compose.yml \
#       start docspell-restserver docspell-joex
#
# Over SSH, prefix with: ssh "${DOCSPELL_SSH_HOST:-pve}" '<command>'
#
