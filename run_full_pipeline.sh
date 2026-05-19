#!/bin/bash
# Full Docspell pipeline — fresh session, with overrides.
#
# Re-runs the entire triage → classify → apply → enrich → custom-fields → verify → dashboard
# pipeline. All write phases use --apply + confirm phrases and --overwrite where applicable.
# Idempotent end-to-end: re-running over an already-organized collective will skip what's
# already done and only patch deltas.
#
# Usage:
#   cd /Users/dmedarov/CODING/Docspell
#   chmod +x run_full_pipeline.sh
#   export GOOGLE_BOOKS_API_KEY="AIza..."        # optional but recommended
#   ./run_full_pipeline.sh
#
# Per-phase env vars (override defaults):
#   DOCSPELL_URL          (default: https://docspell.medarov.net)
#   DOCSPELL_ACCOUNT      (default: library/dmedarov)
#   DOCSPELL_PASSWORD     (default: prompted per phase)
#   GOOGLE_BOOKS_API_KEY  (optional, makes Phase 4 richer)
#   SKIP_DEDUPE=1         skip dedupe phase
#   SKIP_ORGS=1           skip organization seeding
#   SKIP_ENRICH=1         skip online enrichment + custom fields
#   FORCE_OVERWRITE=1     pass --overwrite on apply_book_enrichment

set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"

DOCSPELL_URL="${DOCSPELL_URL:-https://docspell.medarov.net}"
DOCSPELL_ACCOUNT="${DOCSPELL_ACCOUNT:-library/dmedarov}"

echo "================================================================"
echo "Docspell full pipeline"
echo "  Root:    $ROOT"
echo "  URL:     $DOCSPELL_URL"
echo "  Account: $DOCSPELL_ACCOUNT"
echo "  Started: $(date '+%Y-%m-%d %H:%M:%S %z')"
echo "================================================================"
echo ""

# Ask for password ONCE up front and pass to all phases via env var.
# (Avoids 5+ password prompts.)
if [ -z "${DOCSPELL_PASSWORD:-}" ]; then
    read -srp "Docspell password (for $DOCSPELL_ACCOUNT): " DOCSPELL_PASSWORD
    echo
    export DOCSPELL_PASSWORD
fi
export DOCSPELL_ACCOUNT
export DOCSPELL_URL

# --------------------------------------------------------------------
# Phase 0: backup current state (export of all items via dsc, if installed)
# --------------------------------------------------------------------
echo "[Phase 0] Pre-flight snapshot (best-effort)"
if command -v dsc >/dev/null 2>&1; then
    SNAP_DIR="$ROOT/out/snapshots/$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$SNAP_DIR"
    echo "  Running dsc export → $SNAP_DIR"
    dsc export --target "$SNAP_DIR" || echo "  (dsc export failed — continuing without snapshot)"
else
    echo "  dsc not installed; skipping pre-flight snapshot."
    echo "  Install: brew install dsc"
fi
echo ""

# --------------------------------------------------------------------
# Phase 1: triage — read-only inventory + 19-query search dump
# --------------------------------------------------------------------
echo "[Phase 1] Read-only triage"
python3 docspell_triage.py --url "$DOCSPELL_URL" --account "$DOCSPELL_ACCOUNT"
echo ""

# --------------------------------------------------------------------
# Phase 2: offline name-based classification
# --------------------------------------------------------------------
echo "[Phase 2] Offline classifier (title-based)"
python3 classify_by_name.py
echo ""

# --------------------------------------------------------------------
# Phase 3: CSV migration (Archive→Library, area:X→Book:X, doctype:* normalization)
# --------------------------------------------------------------------
echo "[Phase 3] CSV schema migration"
python3 fix_csv_schema.py
echo ""

# --------------------------------------------------------------------
# Phase 4: apply folder + tags (idempotent, uses PUT /taglink)
# --------------------------------------------------------------------
echo "[Phase 4] Apply folder + tags"
python3 apply_reviewed_actions.py \
    --url "$DOCSPELL_URL" \
    --account "$DOCSPELL_ACCOUNT" \
    --csv out/docspell-name-classification-fixed.csv \
    --apply \
    --confirm APPLY-LIBRARY
echo ""

# --------------------------------------------------------------------
# Phase 5: seed Bulgarian organizations
# --------------------------------------------------------------------
if [ "${SKIP_ORGS:-0}" != "1" ]; then
    echo "[Phase 5] Seed Bulgarian organizations"
    python3 seed_organizations.py \
        --url "$DOCSPELL_URL" \
        --account "$DOCSPELL_ACCOUNT" \
        --apply \
        --confirm SEED-ORGS
    echo ""
else
    echo "[Phase 5] Skipped (SKIP_ORGS=1)"
    echo ""
fi

# --------------------------------------------------------------------
# Phase 6: dedupe (destructive — interactive 2nd prompt required)
# --------------------------------------------------------------------
if [ "${SKIP_DEDUPE:-0}" != "1" ]; then
    echo "[Phase 6] Dedupe duplicate-title items"
    echo "  (Interactive: you'll be asked to type DEDUPE-DELETE at the prompt)"
    python3 dedupe_items.py \
        --url "$DOCSPELL_URL" \
        --account "$DOCSPELL_ACCOUNT" \
        --apply \
        --confirm DEDUPE-DELETE || echo "  (dedupe phase exited non-zero — continuing)"
    echo ""
else
    echo "[Phase 6] Skipped (SKIP_DEDUPE=1)"
    echo ""
fi

# --------------------------------------------------------------------
# Phase 7: online enrichment via Open Library + Google Books
# --------------------------------------------------------------------
if [ "${SKIP_ENRICH:-0}" != "1" ]; then
    echo "[Phase 7] Online enrichment (Open Library + Google Books)"
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
    echo ""

# --------------------------------------------------------------------
# Phase 8: apply enrichment as Docspell custom fields
# --------------------------------------------------------------------
    echo "[Phase 8] Apply enrichment → custom fields"
    OVERWRITE_FLAG=""
    if [ "${FORCE_OVERWRITE:-0}" = "1" ]; then
        OVERWRITE_FLAG="--overwrite"
        echo "  FORCE_OVERWRITE=1 — existing custom-field values WILL be overwritten."
    else
        echo "  Existing custom-field values preserved (set FORCE_OVERWRITE=1 to replace)."
    fi
    python3 apply_book_enrichment.py \
        --url "$DOCSPELL_URL" \
        --account "$DOCSPELL_ACCOUNT" \
        --csv docspell_book_system_enriched/out/books-enriched/book-enrichment.csv \
        --apply \
        --confirm APPLY-ENRICHMENT \
        $OVERWRITE_FLAG
    echo ""
else
    echo "[Phase 7+8] Skipped (SKIP_ENRICH=1)"
    echo ""
fi

# --------------------------------------------------------------------
# Phase 9: verify (read-only health check)
# --------------------------------------------------------------------
echo "[Phase 9] Read-only health verification"
python3 verify_docspell.py --brief --url "$DOCSPELL_URL" --account "$DOCSPELL_ACCOUNT"
echo ""

# --------------------------------------------------------------------
# Phase 10: rebuild dashboard
# --------------------------------------------------------------------
echo "[Phase 10] Rebuild interactive dashboard"
python3 build_dashboard.py
echo ""

# --------------------------------------------------------------------
# Phase 11: optional git commit + push of new logs
# --------------------------------------------------------------------
if [ -d .git ]; then
    echo "[Phase 11] Git status (after pipeline)"
    git status --short
    echo ""
    echo "To commit any new output logs:"
    echo "  git add -A && git commit -m 'Pipeline run $(date +%Y-%m-%d)' && git push"
fi

echo "================================================================"
echo "Pipeline complete at $(date '+%Y-%m-%d %H:%M:%S %z')"
echo "================================================================"
echo ""
echo "Next:"
echo "  open library_dashboard.html"
echo "  cat out/health-report.md"
echo "  cat out/apply-log.csv | wc -l"
echo "  cat out/apply-enrichment-log.csv | wc -l"

# Clear password from env in this shell after we're done
unset DOCSPELL_PASSWORD
