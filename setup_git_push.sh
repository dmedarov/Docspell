#!/bin/bash
# One-shot script: unlock git, move enrichment CSVs in, commit, set remote, push.
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
fi

# --- 2. Move downloaded enrichment CSVs into out/ ---
mkdir -p out/books-enriched
for f in book-enrichment-openlibrary.csv book-enrichment-googlebooks.csv book-enrichment-unified.csv; do
    if [[ -f "$HOME/Downloads/$f" ]]; then
        echo "[3/7] Moving $f to out/books-enriched/"
        mv "$HOME/Downloads/$f" "out/books-enriched/$f"
    fi
done

# --- 3. Ensure git is configured ---
if [[ -z "$(git config user.email 2>/dev/null || true)" ]]; then
    git config user.email "D.Medarov@cnsys.bg"
    git config user.name "Damian Medarov"
fi
echo "[4/7] git user: $(git config user.name) <$(git config user.email)>"

# --- 4. Stage and commit ---
git add -A
echo "[5/7] Staged $(git diff --cached --name-only | wc -l | tr -d ' ') files"

git commit -m "Initial Docspell triage + classification toolkit

- Offline name-based classifier (classify_by_name.py): 692 items classified
  into folder Library / Personal with Book:* topic tags
- Idempotent apply pipeline (apply_reviewed_actions.py): folder + tag updates
  via PUT /sec/item/{id}/folder and PUT /sec/item/{id}/taglink
- Auxiliary scripts: seed_organizations.py, dedupe_items.py, backup_docspell.sh
- Documentation: PLAN.md (data-driven setup), FEATURES.md (full Docspell guide,
  5600 words BG), REPORT.md (session report), CLAUDE.md (working memory)
- Browser-driven online enrichment via Open Library + Google Books:
  259 unique titles enriched with verified metadata (ISBN, publisher, year,
  author, external categories)" || echo "[5/7] Nothing to commit (already up to date)"

# --- 5. Set remote (replace URL if you used a different repo name) ---
if ! git remote get-url origin >/dev/null 2>&1; then
    echo "[6/7] Setting remote origin..."
    git remote add origin https://github.com/dmedarov/Docspell.git
else
    echo "[6/7] Remote 'origin' already set: $(git remote get-url origin)"
fi

# --- 6. Push ---
git branch -M main
echo "[7/7] Pushing to origin/main..."
git push -u origin main

echo ""
echo "Done. View at: https://github.com/dmedarov/Docspell"
