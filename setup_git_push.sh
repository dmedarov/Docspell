#!/bin/bash
# One-shot, idempotent script: unlock git, move enrichment CSVs in,
# commit (if there's anything to commit), set remote (if not already set),
# push current state to origin/main.
#
# Safe to re-run any time.
#
# Run this on your Mac AFTER you've created the empty `Docspell` repository
# on https://github.com/new (username `dmedarov`, repo name `Docspell`).
#
# Usage:
#   cd /Users/dmedarov/CODING/Docspell
#   chmod +x setup_git_push.sh
#   ./setup_git_push.sh

set -euo pipefail

cd "$(dirname "$0")"
echo "[1/7] Working in: $(pwd)"

# --- 1. Remove the stale index.lock that the sandbox left behind ---
if [[ -f .git/index.lock ]]; then
    echo "[2/7] Removing stale .git/index.lock"
    rm -f .git/index.lock
else
    echo "[2/7] No stale .git/index.lock"
fi

# --- 2. Move downloaded enrichment CSVs into out/ (only if present) ---
mkdir -p out/books-enriched
moved=0
for f in book-enrichment-openlibrary.csv book-enrichment-googlebooks.csv book-enrichment-unified.csv; do
    if [[ -f "$HOME/Downloads/$f" ]]; then
        echo "[3/7] Moving $f to out/books-enriched/"
        mv "$HOME/Downloads/$f" "out/books-enriched/$f"
        moved=$((moved + 1))
    fi
done
if [[ "$moved" -eq 0 ]]; then
    echo "[3/7] No downloaded enrichment CSVs to move."
fi

# --- 3. Ensure git is configured ---
if [[ -z "$(git config user.email 2>/dev/null || true)" ]]; then
    git config user.email "D.Medarov@cnsys.bg"
    git config user.name "Damian Medarov"
fi
echo "[4/7] git user: $(git config user.name) <$(git config user.email)>"

# --- 4. Initialize repo if needed ---
if [[ ! -d .git ]]; then
    echo "[4b/7] No .git directory — running git init."
    git init -q
fi

# --- 5. Stage everything and commit only if there's something to commit ---
git add -A
staged_count="$(git diff --cached --name-only | wc -l | tr -d ' ')"
echo "[5/7] Staged ${staged_count} file(s)"

# Detect whether there's at least one commit on the branch yet.
if git rev-parse --verify HEAD >/dev/null 2>&1; then
    HAS_COMMITS=1
else
    HAS_COMMITS=0
fi

if [[ "${staged_count}" -gt 0 ]]; then
    if [[ "${HAS_COMMITS}" -eq 0 ]]; then
        commit_msg="Initial Docspell triage + classification toolkit

- Offline name-based classifier (classify_by_name.py): 692 items classified
  into folder Library / Personal with Book:* topic tags
- Idempotent apply pipeline (apply_reviewed_actions.py): folder + tag updates
  via PUT /sec/item/{id}/folder and PUT /sec/item/{id}/taglink
- Auxiliary scripts: seed_organizations.py, dedupe_items.py, backup_docspell.sh
- Documentation: PLAN.md, FEATURES.md, REPORT.md, CLAUDE.md
- Browser-driven online enrichment via Open Library + Google Books:
  259 unique titles enriched with verified metadata"
    else
        commit_msg="Pipeline update $(date +%Y-%m-%d)"
    fi
    git commit -q -m "${commit_msg}"
    echo "[5/7] Committed: ${commit_msg%%$'\n'*}"
else
    echo "[5/7] Nothing to commit (working tree clean)."
fi

# --- 6. Set remote — only if not already configured ---
if ! git remote get-url origin >/dev/null 2>&1; then
    echo "[6/7] Setting remote origin..."
    git remote add origin https://github.com/dmedarov/Docspell.git
else
    echo "[6/7] Remote 'origin' already set: $(git remote get-url origin)"
fi

# --- 7. Ensure we're on main and push ---
current_branch="$(git symbolic-ref --short -q HEAD 2>/dev/null || echo "")"
if [[ "${current_branch}" != "main" ]]; then
    echo "[7/7] Renaming current branch to main (was: ${current_branch:-detached})"
    git branch -M main
fi

echo "[7/7] Pushing to origin/main..."
git push -u origin main

echo ""
echo "Done. View at: https://github.com/dmedarov/Docspell"
