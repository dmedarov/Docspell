#!/bin/bash
# Full Docspell pipeline — fresh session, with overrides.
#
# Re-runs the entire triage -> classify -> apply -> enrich -> custom-fields
# -> verify -> dashboard pipeline. All write phases use --apply + confirm
# phrases (and --overwrite where applicable). Idempotent end-to-end: re-running
# over an already-organized collective will skip what's already done and only
# patch deltas.
#
# Usage:
#   cd /Users/dmedarov/CODING/Docspell
#   chmod +x run_full_pipeline.sh
#   export GOOGLE_BOOKS_API_KEY="AIza..."        # optional but recommended
#   ./run_full_pipeline.sh
#
# Flags:
#   --dry-run             Run all phases without --apply (preview only).
#   --phases 1,2,3        Comma-separated phase numbers (0..10) to run.
#                         Defaults to all. Phase 0 is the pre-flight snapshot,
#                         phases 1..10 are the main pipeline.
#   --password-file PATH  Read DOCSPELL_PASSWORD from PATH instead of prompt.
#   --help                Show this help and exit.
#
# Per-phase env vars (override defaults):
#   DOCSPELL_URL          (default: https://docspell.medarov.net)
#   DOCSPELL_ACCOUNT      (default: library/dmedarov)
#   DOCSPELL_PASSWORD     (default: prompted unless --password-file)
#   GOOGLE_BOOKS_API_KEY  (optional, makes Phase 4 richer)
#   SKIP_DEDUPE=1         skip dedupe phase
#   SKIP_ORGS=1           skip organization seeding
#   SKIP_ENRICH=1         skip online enrichment + custom fields
#   FORCE_OVERWRITE=1     pass --overwrite on apply_book_enrichment
#
# All output is teed to: out/pipeline-run-<timestamp>.log

set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"

DOCSPELL_URL="${DOCSPELL_URL:-https://docspell.medarov.net}"
DOCSPELL_ACCOUNT="${DOCSPELL_ACCOUNT:-library/dmedarov}"

# -----------------------------------------------------------------------------
# Argument parsing
# -----------------------------------------------------------------------------

DRY_RUN=0
PHASES_REQUESTED=""
PASSWORD_FILE=""

show_help() {
    sed -n '2,32p' "$0"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --phases)
            PHASES_REQUESTED="${2:-}"
            if [ -z "${PHASES_REQUESTED}" ]; then
                echo "ERROR: --phases requires a value" >&2
                exit 2
            fi
            shift 2
            ;;
        --phases=*)
            PHASES_REQUESTED="${1#--phases=}"
            shift
            ;;
        --password-file)
            PASSWORD_FILE="${2:-}"
            if [ -z "${PASSWORD_FILE}" ]; then
                echo "ERROR: --password-file requires a path" >&2
                exit 2
            fi
            shift 2
            ;;
        --password-file=*)
            PASSWORD_FILE="${1#--password-file=}"
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            echo "ERROR: unknown argument: $1" >&2
            echo "Run with --help for usage." >&2
            exit 2
            ;;
    esac
done

# -----------------------------------------------------------------------------
# Phase selection — default is all phases (0..10).
# -----------------------------------------------------------------------------

ALL_PHASES=(0 1 2 3 4 5 6 7 8 9 10)

phase_enabled() {
    local target="$1"
    if [ -z "${PHASES_REQUESTED}" ]; then
        return 0
    fi
    local IFS=','
    # shellcheck disable=SC2206
    local arr=(${PHASES_REQUESTED})
    for p in "${arr[@]}"; do
        if [ "${p}" = "${target}" ]; then
            return 0
        fi
    done
    return 1
}

# -----------------------------------------------------------------------------
# Session log — tee everything from this point on.
# -----------------------------------------------------------------------------

mkdir -p "${ROOT}/out"
SESSION_TS="$(date +%Y%m%d-%H%M%S)"
SESSION_LOG="${ROOT}/out/pipeline-run-${SESSION_TS}.log"

# Use a process substitution so a single tee buffers stdout+stderr to the log.
exec > >(tee -a "${SESSION_LOG}") 2>&1

echo "================================================================"
echo "Docspell full pipeline"
echo "  Root:      $ROOT"
echo "  URL:       $DOCSPELL_URL"
echo "  Account:   $DOCSPELL_ACCOUNT"
echo "  Dry-run:   $DRY_RUN"
echo "  Phases:    ${PHASES_REQUESTED:-all}"
echo "  Log:       $SESSION_LOG"
echo "  Started:   $(date '+%Y-%m-%d %H:%M:%S %z')"
echo "================================================================"
echo ""

# -----------------------------------------------------------------------------
# DNS preflight — extract host from DOCSPELL_URL and resolve it.
# -----------------------------------------------------------------------------

DNS_HOST="$(printf '%s' "${DOCSPELL_URL}" \
    | awk -F/ '{print $3}' \
    | awk -F: '{print $1}')"

if [ -n "${DNS_HOST}" ]; then
    echo "[preflight] Resolving ${DNS_HOST}..."
    if ! python3 -c "import socket,sys; socket.gethostbyname('${DNS_HOST}')" 2>/dev/null; then
        echo "ERROR: DNS resolution for '${DNS_HOST}' failed."
        echo "       Likely Tailscale/DNS issue."
        echo "  Try: sudo tailscale up --accept-dns --accept-routes --exit-node-allow-lan-access"
        echo "  Or:  echo '100.66.18.7  ${DNS_HOST}' | sudo tee -a /etc/hosts"
        exit 2
    fi
    echo "[preflight] OK"
    echo ""
fi

# -----------------------------------------------------------------------------
# Password handling — once, up front. --password-file takes precedence
# over prompt; existing env var still wins over both (per original behavior).
# -----------------------------------------------------------------------------

if [ -z "${DOCSPELL_PASSWORD:-}" ]; then
    if [ -n "${PASSWORD_FILE}" ]; then
        if [ ! -r "${PASSWORD_FILE}" ]; then
            echo "ERROR: password file not readable: ${PASSWORD_FILE}" >&2
            exit 2
        fi
        # Strip trailing newline only.
        DOCSPELL_PASSWORD="$(tr -d '\r' < "${PASSWORD_FILE}" | sed -e 's/[[:space:]]*$//')"
        export DOCSPELL_PASSWORD
        echo "[auth] Password loaded from ${PASSWORD_FILE}"
    else
        read -srp "Docspell password (for $DOCSPELL_ACCOUNT): " DOCSPELL_PASSWORD
        echo
        export DOCSPELL_PASSWORD
    fi
fi
export DOCSPELL_ACCOUNT
export DOCSPELL_URL

# -----------------------------------------------------------------------------
# Results tracking — bash 3 compatible (parallel indexed arrays).
# macOS ships bash 3.x; do NOT depend on associative arrays.
# -----------------------------------------------------------------------------

PHASE_NAMES=()
PHASE_STATUS=()   # "ok" | "fail:<rc>" | "skipped:<reason>"

record() {
    local name="$1" status="$2"
    PHASE_NAMES+=("${name}")
    PHASE_STATUS+=("${status}")
}

run_phase() {
    local id="$1" name="$2"
    shift 2
    if ! phase_enabled "${id}"; then
        echo "[Phase ${id}] ${name} — SKIPPED (not in --phases)"
        echo ""
        record "Phase ${id}: ${name}" "skipped:not-selected"
        return 0
    fi
    echo "[Phase ${id}] ${name}"
    if "$@"; then
        record "Phase ${id}: ${name}" "ok"
    else
        local rc=$?
        record "Phase ${id}: ${name}" "fail:${rc}"
        echo "  (phase ${id} exited non-zero — continuing)"
    fi
    echo ""
}

# Helper: pass --apply unless DRY_RUN is set.
apply_flag() {
    if [ "${DRY_RUN}" = "1" ]; then
        printf ''
    else
        printf -- '--apply'
    fi
}

confirm_flag() {
    local phrase="$1"
    if [ "${DRY_RUN}" = "1" ]; then
        printf ''
    else
        printf -- '--confirm\n%s' "${phrase}"
    fi
}

# Build a phase-runner that injects --apply --confirm <phrase> only when not dry-run.
maybe_apply_args() {
    local phrase="$1"
    if [ "${DRY_RUN}" = "1" ]; then
        return
    fi
    printf -- '--apply\n--confirm\n%s' "${phrase}"
}

PIPELINE_START="$(date +%s)"

# -----------------------------------------------------------------------------
# Phase 0: pre-flight snapshot (best-effort)
# -----------------------------------------------------------------------------

phase0() {
    if command -v dsc >/dev/null 2>&1; then
        local snap_dir="$ROOT/out/snapshots/$(date +%Y%m%d-%H%M%S)"
        mkdir -p "$snap_dir"
        echo "  Running dsc export -> $snap_dir"
        dsc export --target "$snap_dir" || echo "  (dsc export failed — continuing without snapshot)"
    else
        echo "  dsc not installed; skipping pre-flight snapshot."
        echo "  Install: brew install dsc"
    fi
}
run_phase 0 "Pre-flight snapshot" phase0

# -----------------------------------------------------------------------------
# Phase 1: triage — read-only inventory
# -----------------------------------------------------------------------------

phase1() {
    python3 docspell_triage.py --url "$DOCSPELL_URL" --account "$DOCSPELL_ACCOUNT"
}
run_phase 1 "Read-only triage" phase1

# -----------------------------------------------------------------------------
# Phase 2: offline name-based classification
# -----------------------------------------------------------------------------

phase2() {
    python3 classify_by_name.py
}
run_phase 2 "Offline classifier (title-based)" phase2

# -----------------------------------------------------------------------------
# Phase 3: CSV migration
# -----------------------------------------------------------------------------

phase3() {
    python3 fix_csv_schema.py
}
run_phase 3 "CSV schema migration" phase3

# -----------------------------------------------------------------------------
# Phase 4: apply folder + tags
# -----------------------------------------------------------------------------

phase4() {
    local args=(
        --url "$DOCSPELL_URL"
        --account "$DOCSPELL_ACCOUNT"
        --csv out/docspell-name-classification-fixed.csv
    )
    if [ "${DRY_RUN}" != "1" ]; then
        args+=(--apply --confirm APPLY-LIBRARY)
    else
        echo "  (dry-run: omitting --apply)"
    fi
    python3 apply_reviewed_actions.py "${args[@]}"
}
run_phase 4 "Apply folder + tags" phase4

# -----------------------------------------------------------------------------
# Phase 5: seed organizations
# -----------------------------------------------------------------------------

phase5() {
    if [ "${SKIP_ORGS:-0}" = "1" ]; then
        echo "  (SKIP_ORGS=1)"
        return 0
    fi
    local args=(
        --url "$DOCSPELL_URL"
        --account "$DOCSPELL_ACCOUNT"
    )
    if [ "${DRY_RUN}" != "1" ]; then
        args+=(--apply --confirm SEED-ORGS)
    else
        echo "  (dry-run: omitting --apply)"
    fi
    python3 seed_organizations.py "${args[@]}"
}
run_phase 5 "Seed Bulgarian organizations" phase5

# -----------------------------------------------------------------------------
# Phase 6: dedupe (destructive)
# -----------------------------------------------------------------------------

phase6() {
    if [ "${SKIP_DEDUPE:-0}" = "1" ]; then
        echo "  (SKIP_DEDUPE=1)"
        return 0
    fi
    echo "  (Interactive: you'll be asked to type DEDUPE-DELETE at the prompt)"
    local args=(
        --url "$DOCSPELL_URL"
        --account "$DOCSPELL_ACCOUNT"
    )
    if [ "${DRY_RUN}" != "1" ]; then
        args+=(--apply --confirm DEDUPE-DELETE)
    else
        echo "  (dry-run: omitting --apply)"
    fi
    python3 dedupe_items.py "${args[@]}"
}
run_phase 6 "Dedupe duplicate-title items" phase6

# -----------------------------------------------------------------------------
# Phase 7: online enrichment
# -----------------------------------------------------------------------------

phase7() {
    if [ "${SKIP_ENRICH:-0}" = "1" ]; then
        echo "  (SKIP_ENRICH=1)"
        return 0
    fi
    if [ -n "${GOOGLE_BOOKS_API_KEY:-}" ]; then
        echo "  GOOGLE_BOOKS_API_KEY is set — using both providers."
    else
        echo "  WARNING: GOOGLE_BOOKS_API_KEY not set — Google Books may rate-limit."
    fi
    cd "$ROOT/docspell_book_system_enriched"
    python3 docspell_book_classifier.py classify-csv \
        --input ../out/docspell-actions.csv \
        --out out/books-enriched \
        --online-enrich \
        --online-provider both \
        --online-max-results 5 \
        --online-delay 0.2 \
        --online-timeout 15
    cd "$ROOT"
}
run_phase 7 "Online enrichment (Open Library + Google Books)" phase7

# -----------------------------------------------------------------------------
# Phase 8: apply enrichment as custom fields
# -----------------------------------------------------------------------------

phase8() {
    if [ "${SKIP_ENRICH:-0}" = "1" ]; then
        echo "  (SKIP_ENRICH=1)"
        return 0
    fi
    local args=(
        --url "$DOCSPELL_URL"
        --account "$DOCSPELL_ACCOUNT"
        --csv docspell_book_system_enriched/out/books-enriched/book-enrichment.csv
    )
    if [ "${DRY_RUN}" != "1" ]; then
        args+=(--apply --confirm APPLY-ENRICHMENT)
    else
        echo "  (dry-run: omitting --apply)"
    fi
    if [ "${FORCE_OVERWRITE:-0}" = "1" ]; then
        args+=(--overwrite)
        echo "  FORCE_OVERWRITE=1 — existing custom-field values WILL be overwritten."
    else
        echo "  Existing custom-field values preserved (set FORCE_OVERWRITE=1 to replace)."
    fi
    python3 apply_book_enrichment.py "${args[@]}"
}
run_phase 8 "Apply enrichment -> custom fields" phase8

# -----------------------------------------------------------------------------
# Phase 9: verify
# -----------------------------------------------------------------------------

phase9() {
    python3 verify_docspell.py --brief --url "$DOCSPELL_URL" --account "$DOCSPELL_ACCOUNT"
}
run_phase 9 "Read-only health verification" phase9

# -----------------------------------------------------------------------------
# Phase 10: rebuild dashboard
# -----------------------------------------------------------------------------

phase10() {
    python3 build_dashboard.py
}
run_phase 10 "Rebuild interactive dashboard" phase10

# -----------------------------------------------------------------------------
# Final summary
# -----------------------------------------------------------------------------

PIPELINE_END="$(date +%s)"
ELAPSED=$(( PIPELINE_END - PIPELINE_START ))

if [ -d .git ]; then
    echo "Git status (after pipeline):"
    git status --short || true
    echo ""
    echo "To commit any new output logs:"
    echo "  git add -A && git commit -m 'Pipeline run $(date +%Y-%m-%d)' && git push"
    echo ""
fi

echo "================================================================"
echo "Pipeline summary"
echo "================================================================"
printf '  %-50s  %s\n' "PHASE" "STATUS"
printf '  %-50s  %s\n' "-----" "------"
TOTAL=${#PHASE_NAMES[@]}
OK=0; FAILED=0; SKIPPED=0
for i in "${!PHASE_NAMES[@]}"; do
    status="${PHASE_STATUS[$i]}"
    printf '  %-50s  %s\n' "${PHASE_NAMES[$i]}" "${status}"
    case "${status}" in
        ok) OK=$((OK + 1)) ;;
        fail:*) FAILED=$((FAILED + 1)) ;;
        skipped:*) SKIPPED=$((SKIPPED + 1)) ;;
    esac
done
echo "----------------------------------------------------------------"
printf '  Total: %d   ok: %d   failed: %d   skipped: %d\n' \
    "${TOTAL}" "${OK}" "${FAILED}" "${SKIPPED}"
printf '  Elapsed: %ds  (%dm %ds)\n' \
    "${ELAPSED}" "$((ELAPSED / 60))" "$((ELAPSED % 60))"
echo "  Log:     ${SESSION_LOG}"
echo "  Ended:   $(date '+%Y-%m-%d %H:%M:%S %z')"
echo "================================================================"
echo ""
echo "Next:"
echo "  open library_dashboard.html"
echo "  cat out/health-report.md"
echo "  cat out/apply-log.csv | wc -l"
echo "  cat out/apply-enrichment-log.csv | wc -l"

# Clear password from env in this shell after we're done.
unset DOCSPELL_PASSWORD

if [ "${FAILED}" -gt 0 ]; then
    exit 1
fi
