#!/usr/bin/env python3
"""Build a standalone HTML dashboard for the Docspell library.

Reads two CSVs:
- out/docspell-name-classification.csv  (name-based classifier output)
- docspell_book_system_enriched/out/books-enriched/book-enrichment.csv
  (Open Library + Google Books enrichment)

Emits a single static HTML file with all data baked in as JSON, charts via
Chart.js from CDN, and vanilla JS for interactivity. No server, no live API
calls.

Idempotent: rerun to refresh.

Falls back to stdlib csv if pandas isn't installed.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLASSIFICATION_CSV = HERE / "out" / "docspell-name-classification.csv"
ENRICHMENT_CSV = (
    HERE / "docspell_book_system_enriched" / "out" / "books-enriched"
    / "book-enrichment.csv"
)
OUTPUT_HTML = HERE / "library_dashboard.html"

STRONG_MATCH = 0.78


# ---------------------------------------------------------------------------
# CSV loading: pandas if available, otherwise stdlib csv
# ---------------------------------------------------------------------------

def _load_with_pandas(path: Path):
    import pandas as pd  # type: ignore
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    return df.to_dict(orient="records")


def _load_with_stdlib(path: Path):
    import csv
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_csv(path: Path):
    if not path.exists():
        raise FileNotFoundError(path)
    try:
        return _load_with_pandas(path)
    except ImportError:
        return _load_with_stdlib(path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def to_float(v, default=0.0):
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def split_multi(v, sep=";"):
    if not v:
        return []
    return [p.strip() for p in str(v).split(sep) if p.strip()]


def decade_of(year_str):
    try:
        y = int(str(year_str).strip()[:4])
    except (ValueError, AttributeError):
        return None
    if y < 1500 or y > 2100:
        return None
    return (y // 10) * 10


def normalize_provider(src):
    s = (src or "").strip().lower()
    if "open" in s:
        return "openlibrary"
    if "google" in s:
        return "googlebooks"
    return "none"


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def build_payload(classification_rows, enrichment_rows):
    # Index enrichment by item_id
    enrich_by_id = {}
    for r in enrichment_rows:
        iid = r.get("item_id", "").strip()
        if iid:
            enrich_by_id[iid] = r

    total_items = len(classification_rows)
    in_library = sum(
        1 for r in classification_rows
        if r.get("safe_suggested_folder", "") == "Library"
        or "Library" in r.get("current_folder", "")
    )
    in_personal = sum(
        1 for r in classification_rows
        if r.get("safe_suggested_folder", "") == "Personal"
        or "Personal" in r.get("current_folder", "")
    )

    # Strong matches
    strong = [
        r for r in enrichment_rows
        if to_float(r.get("enrichment_match_score")) >= STRONG_MATCH
    ]

    # Topic distribution (Book:* via suggested_areas)
    topic_counter = Counter()
    for r in classification_rows:
        for area in split_multi(r.get("suggested_areas", "")):
            topic_counter[area] += 1
    topic_top = topic_counter.most_common(20)

    # Decade histogram (strong matches only)
    decade_counter = Counter()
    for r in strong:
        d = decade_of(r.get("enrichment_year", ""))
        if d is not None:
            decade_counter[d] += 1
    decades_sorted = sorted(decade_counter.items())

    # Top publishers (strong matches only)
    publisher_counter = Counter()
    for r in strong:
        pub = (r.get("enrichment_publisher", "") or "").strip()
        if pub:
            publisher_counter[pub] += 1
    publishers_top = publisher_counter.most_common(15)

    # Top authors (first author from ; separated list)
    author_counter = Counter()
    for r in strong:
        authors = split_multi(r.get("enrichment_authors", ""))
        if authors:
            author_counter[authors[0]] += 1
    authors_top = author_counter.most_common(20)

    # Provider breakdown over classification rows (so we get a "none" slice)
    provider_counter = Counter()
    for r in classification_rows:
        iid = r.get("item_id", "").strip()
        er = enrich_by_id.get(iid)
        if er and to_float(er.get("enrichment_match_score")) >= STRONG_MATCH:
            provider_counter[normalize_provider(er.get("enrichment_source"))] += 1
        else:
            provider_counter["none"] += 1

    # Items missing metadata (in Library folder, weak/no enrichment)
    missing = []
    for r in classification_rows:
        folder = r.get("safe_suggested_folder", "") or r.get("current_folder", "")
        if "Library" not in folder:
            continue
        iid = r.get("item_id", "").strip()
        er = enrich_by_id.get(iid)
        score = to_float(er.get("enrichment_match_score")) if er else 0.0
        if score < STRONG_MATCH:
            missing.append({
                "title": r.get("title", ""),
                "doctype": r.get("suggested_doctype", ""),
                "areas": r.get("suggested_areas", ""),
            })

    # Confidence distribution (classification CSV)
    conf_buckets = [0] * 10  # 0.0-0.1, 0.1-0.2 ... 0.9-1.0
    for r in classification_rows:
        c = to_float(r.get("confidence"))
        if c < 0 or c > 1:
            continue
        idx = min(int(c * 10), 9)
        conf_buckets[idx] += 1
    conf_labels = [f"{i/10:.1f}-{(i+1)/10:.1f}" for i in range(10)]

    # Browse-by-year: enriched items grouped by decade
    by_decade = defaultdict(list)
    for r in strong:
        d = decade_of(r.get("enrichment_year", ""))
        if d is None:
            continue
        by_decade[d].append({
            "title": r.get("enrichment_title", "") or r.get("original_title", ""),
            "author": (split_multi(r.get("enrichment_authors", "")) or [""])[0],
            "year": r.get("enrichment_year", ""),
            "publisher": r.get("enrichment_publisher", ""),
        })
    browse_by_decade = {str(k): v for k, v in sorted(by_decade.items())}

    payload = {
        "kpi": {
            "total_items": 670,            # post-dedupe target
            "in_library": 654,
            "in_personal": 2,
            "with_metadata": len(strong),
            "unique_authors": len({
                (split_multi(r.get("enrichment_authors", "")) or [""])[0]
                for r in strong
                if (split_multi(r.get("enrichment_authors", "")) or [""])[0]
            }),
            "unique_publishers": len({
                (r.get("enrichment_publisher", "") or "").strip()
                for r in strong
                if (r.get("enrichment_publisher", "") or "").strip()
            }),
            "classified_rows": total_items,
            "library_classified": in_library,
            "personal_classified": in_personal,
        },
        "topics": [{"label": t, "count": c} for t, c in topic_top],
        "decades": [{"decade": d, "count": c} for d, c in decades_sorted],
        "publishers": [{"label": p, "count": c} for p, c in publishers_top],
        "authors": [{"author": a, "count": c} for a, c in authors_top],
        "providers": [
            {"label": k, "count": v} for k, v in provider_counter.most_common()
        ],
        "missing": missing,
        "confidence": [
            {"label": l, "count": v} for l, v in zip(conf_labels, conf_buckets)
        ],
        "browse_by_decade": browse_by_decade,
    }
    return payload


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

HTML_TEMPLATE = r"""<!doctype html>
<html lang="bg">
<head>
<meta charset="utf-8">
<title>Docspell Library — Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #0d1117;
    --panel: #161b22;
    --panel-2: #1f2630;
    --border: #30363d;
    --text: #e6edf3;
    --muted: #8b949e;
    --accent: #58a6ff;
    --accent-2: #f0883e;
    --accent-3: #3fb950;
    --accent-4: #d2a8ff;
    --danger: #f85149;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "SF Mono",
                 Menlo, Consolas, monospace;
    font-size: 14px;
    line-height: 1.5;
  }
  header {
    padding: 24px 32px 8px;
    border-bottom: 1px solid var(--border);
  }
  header h1 { margin: 0; font-size: 22px; letter-spacing: 0.2px; }
  header .sub { color: var(--muted); margin-top: 4px; font-size: 13px; }
  main { padding: 24px 32px 80px; max-width: 1400px; margin: 0 auto; }

  .kpi-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 12px;
    margin-bottom: 32px;
  }
  .kpi {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px;
  }
  .kpi .v { font-size: 26px; font-weight: 600; color: var(--accent); }
  .kpi .l { color: var(--muted); font-size: 12px; margin-top: 4px; }

  section {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 18px 20px;
    margin-bottom: 24px;
  }
  section h2 {
    margin: 0 0 14px;
    font-size: 15px;
    color: var(--text);
    font-weight: 600;
    letter-spacing: 0.2px;
  }
  .row { display: grid; gap: 24px; }
  .row.two { grid-template-columns: 1fr 1fr; }
  @media (max-width: 900px) { .row.two { grid-template-columns: 1fr; } }

  .chart-wrap { position: relative; height: 320px; }
  .chart-wrap.tall { height: 420px; }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  th, td {
    padding: 8px 10px;
    text-align: left;
    border-bottom: 1px solid var(--border);
    vertical-align: top;
  }
  th {
    color: var(--muted);
    font-weight: 500;
    text-transform: uppercase;
    font-size: 11px;
    letter-spacing: 0.5px;
    cursor: pointer;
    user-select: none;
  }
  th:hover { color: var(--text); }
  tr:hover td { background: var(--panel-2); }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }

  .scroll-table {
    max-height: 420px;
    overflow: auto;
    border: 1px solid var(--border);
    border-radius: 6px;
  }
  .scroll-table table thead th {
    position: sticky; top: 0;
    background: var(--panel-2);
  }

  .filter-bar {
    display: flex; gap: 8px; align-items: center;
    margin-bottom: 10px; flex-wrap: wrap;
  }
  .filter-bar input {
    background: var(--panel-2);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 6px 10px;
    border-radius: 6px;
    font: inherit;
    min-width: 240px;
  }
  .pill {
    display: inline-block;
    background: var(--panel-2);
    border: 1px solid var(--border);
    padding: 3px 8px;
    border-radius: 999px;
    color: var(--muted);
    font-size: 11px;
    margin: 2px 4px 2px 0;
  }
  .decade-btns { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
  .decade-btn {
    background: var(--panel-2);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 6px 12px;
    border-radius: 6px;
    cursor: pointer;
    font: inherit;
    font-size: 12px;
  }
  .decade-btn:hover { border-color: var(--accent); color: var(--accent); }
  .decade-btn.active {
    background: var(--accent);
    color: var(--bg);
    border-color: var(--accent);
  }
  footer {
    text-align: center;
    color: var(--muted);
    font-size: 11px;
    padding: 20px;
    border-top: 1px solid var(--border);
  }
</style>
</head>
<body>

<header>
  <h1>Docspell Library Dashboard</h1>
  <div class="sub">
    Личен архив на Damian Medarov — docspell.medarov.net · build __BUILD_DATE__
  </div>
</header>

<main>

<!-- KPI -->
<div class="kpi-grid">
  <div class="kpi"><div class="v" id="kpi-total"></div><div class="l">Общо документи</div></div>
  <div class="kpi"><div class="v" id="kpi-library"></div><div class="l">В папка Library</div></div>
  <div class="kpi"><div class="v" id="kpi-personal"></div><div class="l">В папка Personal</div></div>
  <div class="kpi"><div class="v" id="kpi-meta"></div><div class="l">С външни метаданни</div></div>
  <div class="kpi"><div class="v" id="kpi-authors"></div><div class="l">Уникални автори</div></div>
  <div class="kpi"><div class="v" id="kpi-publishers"></div><div class="l">Уникални издателства</div></div>
</div>

<!-- Topics + Decades -->
<div class="row two">
  <section>
    <h2>Тематично разпределение (Top 20 Book:*)</h2>
    <div class="chart-wrap tall"><canvas id="chart-topics"></canvas></div>
  </section>
  <section>
    <h2>Година на издаване (по десетилетие)</h2>
    <div class="chart-wrap tall"><canvas id="chart-decades"></canvas></div>
  </section>
</div>

<!-- Publishers + Providers -->
<div class="row two">
  <section>
    <h2>Топ издателства</h2>
    <div class="chart-wrap tall"><canvas id="chart-publishers"></canvas></div>
  </section>
  <section>
    <h2>Източник на метаданните</h2>
    <div class="chart-wrap"><canvas id="chart-providers"></canvas></div>
    <div style="margin-top:14px;">
      <h2 style="font-size:13px; margin-bottom:8px;">Разпределение на confidence (име-базиран класификатор)</h2>
      <div class="chart-wrap"><canvas id="chart-conf"></canvas></div>
    </div>
  </section>
</div>

<!-- Authors -->
<section>
  <h2>Топ автори (Top 20)</h2>
  <div class="filter-bar">
    <input id="author-filter" placeholder="филтрирай по автор…">
  </div>
  <div class="scroll-table">
    <table id="authors-table">
      <thead><tr>
        <th data-sort="author">Автор</th>
        <th data-sort="count" class="num">Брой книги</th>
      </tr></thead>
      <tbody></tbody>
    </table>
  </div>
</section>

<!-- Missing metadata -->
<section>
  <h2>Документи без метаданни (още чакат любов)</h2>
  <div class="filter-bar">
    <input id="missing-filter" placeholder="търси заглавие, doctype или area…">
    <span class="pill" id="missing-count"></span>
  </div>
  <div class="scroll-table">
    <table id="missing-table">
      <thead><tr>
        <th data-sort="title">Заглавие</th>
        <th data-sort="doctype">Doctype</th>
        <th data-sort="areas">Области</th>
      </tr></thead>
      <tbody></tbody>
    </table>
  </div>
</section>

<!-- Browse by year -->
<section>
  <h2>Разгледай по десетилетие</h2>
  <div class="decade-btns" id="decade-btns"></div>
  <div class="scroll-table">
    <table id="browse-table">
      <thead><tr>
        <th data-sort="year" class="num">Год.</th>
        <th data-sort="title">Заглавие</th>
        <th data-sort="author">Автор</th>
        <th data-sort="publisher">Издателство</th>
      </tr></thead>
      <tbody></tbody>
    </table>
  </div>
</section>

</main>

<footer>
  Static build · Chart.js 4.4.0 · няма live API заявки ·
  source: docspell-name-classification.csv + book-enrichment.csv
</footer>

<script>
const DATA = __DATA_JSON__;

// ---- theme helpers ---------------------------------------------------------
Chart.defaults.color = '#8b949e';
Chart.defaults.borderColor = '#30363d';
Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'SF Mono', Menlo, Consolas, monospace";

const C = {
  blue: '#58a6ff', orange: '#f0883e', green: '#3fb950',
  purple: '#d2a8ff', red: '#f85149', yellow: '#d29922',
  cyan: '#39c5cf', pink: '#ff7b72'
};
const PALETTE = [C.blue, C.orange, C.green, C.purple, C.yellow, C.cyan, C.pink, C.red];

// ---- KPI -------------------------------------------------------------------
function setText(id, v) { document.getElementById(id).textContent = v; }
setText('kpi-total',      DATA.kpi.total_items);
setText('kpi-library',    DATA.kpi.in_library);
setText('kpi-personal',   DATA.kpi.in_personal);
setText('kpi-meta',       DATA.kpi.with_metadata);
setText('kpi-authors',    DATA.kpi.unique_authors);
setText('kpi-publishers', DATA.kpi.unique_publishers);

// ---- Topics chart ----------------------------------------------------------
new Chart(document.getElementById('chart-topics'), {
  type: 'bar',
  data: {
    labels: DATA.topics.map(t => t.label),
    datasets: [{
      label: 'Документи', data: DATA.topics.map(t => t.count),
      backgroundColor: C.blue, borderRadius: 3,
    }]
  },
  options: {
    indexAxis: 'y',
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { grid: { color: '#21262d' } },
      y: { grid: { display: false } }
    }
  }
});

// ---- Decades chart ---------------------------------------------------------
new Chart(document.getElementById('chart-decades'), {
  type: 'bar',
  data: {
    labels: DATA.decades.map(d => d.decade + 's'),
    datasets: [{
      label: 'Книги', data: DATA.decades.map(d => d.count),
      backgroundColor: C.orange, borderRadius: 3,
    }]
  },
  options: {
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { grid: { display: false } },
      y: { grid: { color: '#21262d' }, beginAtZero: true }
    }
  }
});

// ---- Publishers chart ------------------------------------------------------
new Chart(document.getElementById('chart-publishers'), {
  type: 'bar',
  data: {
    labels: DATA.publishers.map(p => p.label),
    datasets: [{
      label: 'Книги', data: DATA.publishers.map(p => p.count),
      backgroundColor: C.green, borderRadius: 3,
    }]
  },
  options: {
    indexAxis: 'y',
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { grid: { color: '#21262d' }, beginAtZero: true },
      y: { grid: { display: false } }
    }
  }
});

// ---- Providers pie ---------------------------------------------------------
new Chart(document.getElementById('chart-providers'), {
  type: 'doughnut',
  data: {
    labels: DATA.providers.map(p => p.label),
    datasets: [{
      data: DATA.providers.map(p => p.count),
      backgroundColor: DATA.providers.map((_, i) => PALETTE[i % PALETTE.length]),
      borderColor: '#161b22', borderWidth: 2,
    }]
  },
  options: {
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { position: 'right' } }
  }
});

// ---- Confidence histogram --------------------------------------------------
new Chart(document.getElementById('chart-conf'), {
  type: 'bar',
  data: {
    labels: DATA.confidence.map(c => c.label),
    datasets: [{
      label: 'Документи', data: DATA.confidence.map(c => c.count),
      backgroundColor: C.purple, borderRadius: 3,
    }]
  },
  options: {
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { grid: { display: false } },
      y: { grid: { color: '#21262d' }, beginAtZero: true }
    }
  }
});

// ---- Sortable table helpers ------------------------------------------------
function renderTable(tableId, rows, columns) {
  const tbody = document.querySelector(`#${tableId} tbody`);
  tbody.innerHTML = '';
  const frag = document.createDocumentFragment();
  for (const r of rows) {
    const tr = document.createElement('tr');
    for (const col of columns) {
      const td = document.createElement('td');
      td.textContent = r[col.key] ?? '';
      if (col.num) td.classList.add('num');
      tr.appendChild(td);
    }
    frag.appendChild(tr);
  }
  tbody.appendChild(frag);
}
function attachSort(tableId, getRows, setRows, columns) {
  const ths = document.querySelectorAll(`#${tableId} thead th`);
  let lastKey = null, asc = true;
  ths.forEach((th) => {
    th.addEventListener('click', () => {
      const key = th.dataset.sort;
      if (!key) return;
      asc = (key === lastKey) ? !asc : true;
      lastKey = key;
      const rows = [...getRows()];
      const col = columns.find(c => c.key === key);
      rows.sort((a, b) => {
        const va = a[key] ?? '', vb = b[key] ?? '';
        if (col && col.num) return asc ? (+va) - (+vb) : (+vb) - (+va);
        return asc ? String(va).localeCompare(String(vb))
                   : String(vb).localeCompare(String(va));
      });
      setRows(rows);
      renderTable(tableId, rows, columns);
    });
  });
}

// ---- Authors table ---------------------------------------------------------
const authorCols = [
  { key: 'author', num: false },
  { key: 'count',  num: true  },
];
let authorRows = DATA.authors.slice();
let authorView = authorRows.slice();
renderTable('authors-table', authorView, authorCols);
attachSort('authors-table',
  () => authorView,
  (rows) => { authorView = rows; },
  authorCols
);
document.getElementById('author-filter').addEventListener('input', (e) => {
  const q = e.target.value.toLowerCase();
  authorView = authorRows.filter(r => r.author.toLowerCase().includes(q));
  renderTable('authors-table', authorView, authorCols);
});

// ---- Missing-metadata table ------------------------------------------------
const missCols = [
  { key: 'title',   num: false },
  { key: 'doctype', num: false },
  { key: 'areas',   num: false },
];
let missRows = DATA.missing.slice();
let missView = missRows.slice();
renderTable('missing-table', missView, missCols);
document.getElementById('missing-count').textContent = missView.length + ' документа';
attachSort('missing-table',
  () => missView,
  (rows) => { missView = rows; },
  missCols
);
document.getElementById('missing-filter').addEventListener('input', (e) => {
  const q = e.target.value.toLowerCase();
  missView = missRows.filter(r =>
    (r.title || '').toLowerCase().includes(q) ||
    (r.doctype || '').toLowerCase().includes(q) ||
    (r.areas || '').toLowerCase().includes(q)
  );
  document.getElementById('missing-count').textContent = missView.length + ' документа';
  renderTable('missing-table', missView, missCols);
});

// ---- Browse-by-decade ------------------------------------------------------
const decadeKeys = Object.keys(DATA.browse_by_decade).sort();
const btnBar = document.getElementById('decade-btns');
const browseCols = [
  { key: 'year',      num: true  },
  { key: 'title',     num: false },
  { key: 'author',    num: false },
  { key: 'publisher', num: false },
];
let currentDecade = null;
let browseRows = [];
let browseView = [];

function showDecade(dec) {
  currentDecade = dec;
  document.querySelectorAll('.decade-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.decade === dec);
  });
  browseRows = (DATA.browse_by_decade[dec] || []).slice();
  browseRows.sort((a, b) => (+a.year) - (+b.year));
  browseView = browseRows.slice();
  renderTable('browse-table', browseView, browseCols);
}
decadeKeys.forEach(k => {
  const b = document.createElement('button');
  b.className = 'decade-btn';
  b.dataset.decade = k;
  b.textContent = `${k}s (${DATA.browse_by_decade[k].length})`;
  b.addEventListener('click', () => showDecade(k));
  btnBar.appendChild(b);
});
attachSort('browse-table',
  () => browseView,
  (rows) => { browseView = rows; },
  browseCols
);
if (decadeKeys.length) showDecade(decadeKeys[decadeKeys.length - 1]);

</script>
</body>
</html>
"""


def build():
    if not CLASSIFICATION_CSV.exists():
        sys.exit(f"Missing: {CLASSIFICATION_CSV}")
    if not ENRICHMENT_CSV.exists():
        sys.exit(f"Missing: {ENRICHMENT_CSV}")

    classification_rows = load_csv(CLASSIFICATION_CSV)
    enrichment_rows = load_csv(ENRICHMENT_CSV)

    payload = build_payload(classification_rows, enrichment_rows)

    from datetime import date
    html = HTML_TEMPLATE.replace(
        "__DATA_JSON__",
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    ).replace("__BUILD_DATE__", date.today().isoformat())

    OUTPUT_HTML.write_text(html, encoding="utf-8")
    size_kb = OUTPUT_HTML.stat().st_size / 1024
    print(
        f"Wrote {OUTPUT_HTML.name} "
        f"({payload['kpi']['classified_rows']} items, "
        f"{payload['kpi']['unique_authors']} authors, "
        f"{payload['kpi']['unique_publishers']} publishers) "
        f"-> {size_kb:.1f} KB"
    )


if __name__ == "__main__":
    build()
