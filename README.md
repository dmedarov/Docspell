# Docspell read-only triage

This workspace contains a local triage script for an existing Docspell instance at:

```sh
https://docspell.medarov.net
```

The script is read-only. It checks the version endpoint, logs in only to obtain an in-memory auth token, runs item searches, and writes local metadata-only outputs.

It does not create, update, delete, tag, confirm, move, download attachments, or fetch full document text.

## Check prerequisites

Check whether `dsc` is installed:

```sh
command -v dsc
```

In this workspace, `dsc` was not found on PATH, so `docspell_triage.py` uses the Docspell HTTP API directly.

Optionally check the Docspell version without logging in:

```sh
python3 docspell_triage.py --version-only
```

## Run

Run the triage with interactive credential prompts:

```sh
python3 docspell_triage.py
```

You can provide the account name without storing the password:

```sh
python3 docspell_triage.py --account YOUR_ACCOUNT
```

Or provide non-secret settings through environment variables:

```sh
DOCSPELL_URL=https://docspell.medarov.net DOCSPELL_ACCOUNT=YOUR_ACCOUNT python3 docspell_triage.py
```

The password prompt is interactive and hidden. Avoid putting passwords, tokens, or cookies in shell history, config files, or this repository.

## Outputs

The script writes:

```text
out/docspell-searches/
out/docspell-actions.csv
out/docspell-summary.md
```

`out/docspell-searches/` contains one sanitized JSON file per query. These files include item metadata such as id, title, state, dates, folder name, tag names, correspondent names, and attachment count. They do not include document body text or attachment content.

`out/docspell-actions.csv` contains conservative suggestions only, with columns:

```text
item_id,title,current_tags,suggested_add_tags,suggested_folder,suggested_correspondent,confidence,reason,source_query
```

The CSV is advisory only. It is not applied to Docspell.

## Search queries

The script runs these read-only searches:

```text
content:"фактура"
content:"invoice"
content:"договор"
content:"contract"
content:"receipt"
content:"касов"
content:"банка"
content:"statement"
content:"застраховка"
content:"policy"
content:"НАП"
content:"A1"
content:"Vivacom"
content:"Yettel"
content:"DSK"
content:"UniCredit"
content:"Postbank"
!exist:folder
inbox:yes
```

## Conservative suggestion rules

- `invoice` / `фактура` suggests tag `invoice`
- `договор` / `contract` suggests tag `contract`
- `receipt` / `касов` suggests tag `receipt`
- `statement` / `банка` / `bank` suggests tag `bank`
- `застраховка` / `policy` suggests tag `insurance`
- `НАП` suggests correspondent `НАП` and tag `tax`
- `A1`, `Vivacom`, and `Yettel` suggest tag `telecom` and the obvious correspondent
- `DSK`, `UniCredit`, and `Postbank` suggest tag `banking` and the obvious correspondent

Existing tags are not repeated in `suggested_add_tags`.
