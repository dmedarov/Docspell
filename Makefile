# Docspell workspace — Makefile
#
# Each target's `## ...` trailing comment is parsed by `make help`.

SHELL := /bin/bash

.PHONY: help triage classify migrate apply seed-orgs dedupe enrich \
        custom-fields verify dashboard pipeline backup

help: ## List available targets
	@echo "Docspell workspace — available targets:"
	@echo ""
	@awk 'BEGIN {FS = ":.*?## "} \
	     /^[a-zA-Z0-9_-]+:.*?## / { \
	         printf "  make %-15s %s\n", $$1, $$2 \
	     }' $(MAKEFILE_LIST)
	@echo ""

triage: ## Read-only inventory dump from Docspell
	python3 docspell_triage.py

classify: ## Offline name-based classifier (CSV output)
	python3 classify_by_name.py

migrate: ## CSV schema migration (Archive->Library, area:->Book:)
	python3 fix_csv_schema.py

apply: ## Apply folder + Book:* tags (idempotent, uses APPLY-LIBRARY)
	python3 apply_reviewed_actions.py --apply --confirm APPLY-LIBRARY

seed-orgs: ## Seed 30 Bulgarian organizations (uses SEED-ORGS)
	python3 seed_organizations.py --apply --confirm SEED-ORGS

dedupe: ## Delete duplicate-title items (interactive, uses DEDUPE-DELETE)
	python3 dedupe_items.py --apply --confirm DEDUPE-DELETE

enrich: ## Online enrich via OpenLibrary + Google Books
	cd docspell_book_system_enriched && \
	    python3 docspell_book_classifier.py classify-csv \
	        --input ../out/docspell-actions.csv \
	        --out out/books-enriched \
	        --online-enrich \
	        --online-provider both

custom-fields: ## Apply enrichment as custom fields (uses APPLY-ENRICHMENT)
	python3 apply_book_enrichment.py --apply --confirm APPLY-ENRICHMENT

verify: ## Read-only health check (brief mode, cron-friendly)
	python3 verify_docspell.py --brief

dashboard: ## Rebuild library_dashboard.html from local CSVs
	python3 build_dashboard.py

pipeline: ## Run the full end-to-end pipeline (run_full_pipeline.sh)
	./run_full_pipeline.sh

backup: ## Nightly backup (auto-detects dsc/ssh/local mode)
	./backup_docspell.sh
