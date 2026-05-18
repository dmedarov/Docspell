#!/usr/bin/env python3
"""Name-based Docspell item classifier (read-only, offline).

Reads the sanitized JSON files in ``out/docspell-searches/`` (produced by
``docspell_triage.py``), deduplicates items by id, and classifies each item
purely from its **title** using Bulgarian + English keyword dictionaries.

This is intentionally heuristic, transparent, and offline — it makes zero
Docspell API calls. The goal is to replace the noisy content-based triage
(which hits keywords buried inside book bodies) with a cleaner signal:
titles are short, intentional, and almost always match the document type.

Output: ``out/docspell-name-classification.csv`` with one row per item and
columns suitable for review and for ``apply_reviewed_actions.py``:

    item_id, title, in_inbox, current_folder, current_tags,
    current_corr_org, decision, suggested_folder,
    suggested_doctype, suggested_areas, suggested_correspondent,
    confidence, matched_keywords, reasoning

The CSV ALSO emits ``safe_suggested_folder`` and ``safe_add_tags`` columns
in the same shape ``apply_reviewed_actions.py`` already understands, but
under a new ``review_decision`` vocabulary:

    classified       — high-confidence, safe to apply
    needs_review     — heuristics fired but want a human glance
    unclassified     — no keywords matched; truly manual

Tags follow Docspell's native model: ``doctype:invoice`` in the CSV is
shorthand for tag name=invoice, category=doctype. The apply script can
choose to encode the prefix as a Docspell category when creating tags.
"""

from __future__ import annotations

import csv
import json
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


HERE = Path(__file__).resolve().parent
SEARCHES_DIR = HERE / "out" / "docspell-searches"
OUTPUT_CSV = HERE / "out" / "docspell-name-classification.csv"
INBOX_QUERY = "inbox:yes"


# ---------------------------------------------------------------------------
# Keyword dictionaries
# ---------------------------------------------------------------------------
#
# Each entry maps a *normalized* keyword (lowercased Latin/Cyrillic, with
# diacritics stripped from Latin) to its semantic intent. We match using
# whole-word regex on a normalized title to keep this auditable.

DOCTYPE_PATTERNS: list[tuple[str, list[str]]] = [
    # (doctype name, list of regex patterns matched against normalized title)
    # All business doctype patterns are tuned for HIGH PRECISION — they
    # almost never appear in book titles. Polysemous words (policy, report,
    # analysis, tax, legal, regulation, theory) are intentionally NOT used
    # to fire business doctypes; they're handled only as area tags below.
    ("invoice",        [r"\bфактур[аи]?\b", r"\bfaktur[ai]?\b", r"\bинвойс\b", r"\bproforma\b", r"\bпрофактур[аи]?\b", r"\binvoice[\s_\-]*\#?\d", r"\binvoice[\s_\-]*number\b", r"\binv[\s_\-]*\d{3,}"]),
    ("receipt",        [r"\bкасов[аи][\s_\-]*бон\w*\b", r"\bquittance\b", r"\breceipt[\s_\-]*\d", r"\brecepit\b"]),
    ("contract",       [r"\bдоговор[аи]?\b", r"\bспоразумение\b", r"\bnda[\s_\-]+", r"\bmsa[\s_\-]+", r"\bемployment[\s_\-]*contract\b", r"\bтрудов\w*[\s_\-]*договор\w*\b", r"\bsignature[\s_\-]*page\b"]),
    ("bank-statement", [r"\bизвлечени[ея][\s_\-]*по[\s_\-]*сметк\w*\b", r"\bbank[\s_\-]*statement\b", r"\baccount[\s_\-]*statement\b", r"\bстатемент[\s_\-]*на[\s_\-]*сметк\w*\b", r"\bcredit[\s_\-]*card[\s_\-]*statement\b"]),
    ("tax",            [r"\bнап[\s_\-]+", r"\bнои[\s_\-]+", r"\bvat[\s_\-]*declaration\b", r"\bvat[\s_\-]*return\b", r"\bданъчн\w*[\s_\-]*декларация\b", r"\btax[\s_\-]*return\b", r"\btax[\s_\-]*declaration\b", r"\btax[\s_\-]*invoice\b", r"\bgodishen[\s_\-]*danach\w*\b", r"\bвсг[\s_\-]+", r"\bobrazets[\s_\-]+\d"]),
    ("insurance",      [r"\bзастраховк\w*\b", r"\bзастрахователн\w*\b", r"\binsurance[\s_\-]*polic[ye]\b", r"\bauto[\s_\-]*insurance\b", r"\bкаско\b", r"\bkasko\b", r"\bгражданска[\s_\-]*отговорност\b"]),
    ("warranty",       [r"\bwarranty[\s_\-]*card\b", r"\bwarranty[\s_\-]*certificate\b", r"\bгаранцион\w*\b"]),
    ("certificate",    [r"\bcertificate[\s_\-]*of\b", r"\bcertificate[\s_\-]*for\b", r"\bcertificate[\s_\-]*no\b", r"\bcert[\s_\-]*\#", r"\bудостоверен\w*[\s_\-]*за\b", r"\bдиплом[аи]?[\s_\-]+", r"\bORC[\s_\-]*Certificate\b"]),
    ("manual",         [r"\bmanual\b", r"\bръководств\w*\b", r"\bнаръчник\w*\b", r"\bhandbook\b", r"\buser[\s_\-]*guide\b", r"\bservice[\s_\-]*guide\b", r"\bowner[\s_\-]*manual\b", r"\bcatalogue\b", r"\bcatalog\b", r"\bкаталог\w*\b", r"\binstruction\w*\b", r"\binstruktsii\b", r"\bинструкц\w*\b", r"\bdatasheet\b", r"\bspec[\s\-_]*sheet\b", r"\bsetup[\s_\-]*guide\b"]),
    ("book",           [r"\bbook\b", r"\bкнига\b", r"\bучебник\w*\b", r"\btextbook\b", r"\bencyclop[ae]dia\b", r"\bенциклопеди\w*\b", r"\bлекц[иi]\w*\b", r"\blecture\b", r"\btreatise\b", r"\btheory\b", r"\btheories\b", r"\btheoretical\b", r"\bтеори\w*\b", r"\bintroduction[\s\-_]*to\b", r"\bintro[\s\-_]*to\b", r"\bcompendium\b", r"\bprimer\b", r"\bessays?\b", r"\bessai\b", r"\bстат[иi][яи]\w*\b", r"\bcollected[\s\-_]*works?\b"]),
    ("report",         [r"\bannual[\s_\-]*report\b", r"\bquarterly[\s_\-]*report\b", r"\bq[1-4][\s_\-]*report\b", r"\bfinancial[\s_\-]*report\b", r"\bдоклад[\s_\-]*на\w*\b", r"\bодиторски[\s_\-]*доклад\b", r"\bgodishen[\s_\-]*otchet\b"]),
    ("cv",             [r"\bcv[\s_\-]*\d", r"\bcv[\s_\-]*[a-z]+[\s_\-]*[a-z]+", r"\bcurriculum[\s_\-]*vitae\b", r"\bautobiograf\w*\b", r"\bавтобиограф\w*\b", r"\bresume[\s_\-]*[a-z]"]),
    ("id-document",    [r"\bличн[аи][\s\-_]*карт\w*\b", r"\bpassport[\s_\-]*\d", r"\bпаспорт[\s_\-]*на\b", r"\bid[\s\-_]*card\b", r"\bсвидетелств[оа][\s_\-]*за\b"]),
    ("medical",        [r"\bлекарск\w*[\s_\-]*бел\w*\b", r"\bbolnich\w*\b", r"\bболнич\w*[\s_\-]*лист\w*\b", r"\bлабораторн\w*[\s_\-]*резул\w*\b", r"\blab[\s_\-]*result\w*\b", r"\bпрескрипц\w*\b", r"\bprescription[\s_\-]*for\b", r"\bепикриз\w*\b", r"\bепикриза\b", r"\bамбулаторен[\s_\-]*лист\b"]),
    ("presentation",   [r"\bpresentation[\s_\-]*to\b", r"\bpresentation[\s_\-]*for\b", r"\bпрезентаци\w*[\s_\-]*на\b", r"\bslides[\s_\-]*\d"]),
    ("photo-scan",     [r"^img[\s_\-]*\d+", r"^image[\s_\-]*\d+", r"^scan[\s_\-]*\d+", r"^photo[\s_\-]*\d+", r"^foto[\s_\-]*\d+", r"^dscn\d+", r"^dsc\d+"]),
    ("legal",          [r"\bпрокурор\w*\b", r"\bдело[\s_\-]*\d", r"\btribunal\b", r"\bcourt[\s_\-]*order\b", r"\blaw[\s_\-]*firm\b", r"\bsubpoena\b", r"\bpovestka\b", r"\bпризовка\b"]),
    ("registration",   [r"\bregistration[\s_\-]*card\b", r"\bтр[\s\-_]*извлечени[ея]\b", r"\bкат[\s_\-]+", r"\bsvid[\s_\-]*registr\w*\b", r"\bталон[\s_\-]*на[\s_\-]*ма\w*\b"]),
    ("ticket",         [r"\bticket[\s_\-]*\#?\d", r"\bбилет[\s_\-]*за\b", r"\bбилет[\s_\-]*\d", r"\bboarding[\s_\-]*pass\b", r"\bboardingpass\b", r"\be[\s_\-]*ticket\b"]),
    ("payslip",        [r"\bpayslip\b", r"\bsalary[\s_\-]*slip\b", r"\bфиш[\s_\-]*за[\s_\-]*заплат\w*\b", r"\bтрз[\s_\-]+"]),
]

# Area is a softer, additive tag-category. An item can have multiple areas.
AREA_PATTERNS: list[tuple[str, list[str]]] = [
    ("banking",        [r"\bbank\w*\b", r"\bбанк\w*\b", r"\bдск\b", r"\bdsk\b", r"\bunicredit\b", r"\bуникредит\w*\b", r"\bбулбанк\w*\b", r"\bpostbank\b", r"\bпощенска[\s_\-]*банк\w*\b", r"\bfibank\b", r"\bпърва[\s_\-]*инвестиц\w*\b", r"\bobb\b", r"\bобб\b", r"\bprocredit\b", r"\bпрокредит\b", r"\ballianz[\s\-_]*bank\b", r"\bалианц[\s\-_]*банк\b", r"\braiffeisen\b", r"\bрайфайзен\b", r"\bccb\b", r"\bцкб\b", r"\bbcc\b", r"\balbank\b", r"\bbnp\b"]),
    ("telecom",        [r"\b[ay]1[\s\-_]*bulgaria\b", r"\ba1[\s\-_]*bg\b", r"\ba1\b", r"\bvivacom\b", r"\bвиваком\b", r"\byettel\b", r"\bйетел\b", r"\btelenor\b", r"\bтеленор\b", r"\bmtel\b", r"\bm-tel\b", r"\bмтел\b", r"\bбтк\b", r"\bsim[\s\-_]*card\b", r"\bтелефон\w*[\s\-_]*сметк\w*\b", r"\bphone[\s\-_]*bill\b"]),
    ("utility-power",  [r"\bevn\b", r"\bевн\b", r"\bcez\b", r"\bчез\b", r"\benergo[\s\-_]*pro\b", r"\bенерго[\s\-_]*про\b", r"\benergo-?pro\b", r"\bелектрораз\w*\b", r"\belectric\w*[\s_\-]*bill\b", r"\bток[\s_\-]*сметк\w*\b"]),
    ("utility-heat",   [r"\bтоплоф\w*\b", r"\btoplofikatsi[ея]\b", r"\bпарно\b", r"\bdistrict[\s\-_]*heating\b"]),
    ("utility-water",  [r"\bsofia[\s\-_]*water\b", r"\bсофийск\w*[\s_\-]*вод\w*\b", r"\bвик\w*\b", r"\bvik\b", r"\bводоснабдяване\b"]),
    ("utility-gas",    [r"\bovergas\b", r"\bовергаз\b", r"\bbulgargaz\b", r"\bбулгаргаз\b", r"\bgas[\s_\-]*bill\b", r"\bгаз[\s_\-]*сметк\w*\b"]),
    ("internet",       [r"\binternet[\s_\-]*bill\b", r"\binternet[\s_\-]*сметк\w*\b", r"\bwifi\b", r"\bnetinfo\b", r"\bfiber\b"]),
    ("car",            [r"\bcar\b", r"\bкол[аи]\b", r"\bавтомобил\w*\b", r"\bmercedes\b", r"\bмерцедес\b", r"\bbmw\b", r"\bбмв\b", r"\baudi\b", r"\bауди\b", r"\bvw\b", r"\bvolkswagen\b", r"\bфолксваген\b", r"\bтойот\w*\b", r"\btoyota\b", r"\bford\b", r"\bфорд\b", r"\bpeugeot\b", r"\bпежо\b", r"\brenault\b", r"\bрено\b", r"\bopel\b", r"\bопел\b", r"\bdacia\b", r"\bдачия\b", r"\boem[\s_\-]*autopart\w*\b", r"\bautopart\w*\b", r"\bкат\b", r"\bnumberplate\b", r"\bsig[\s_\-]*sigurnost\b", r"\bтехническ\w*[\s_\-]*преглед\b", r"\binsurance[\s_\-]*car\b"]),
    ("property",       [r"\bимот\w*\b", r"\bапартамент\w*\b", r"\bкъщ[аи]?\b", r"\bproperty\b", r"\breal[\s_\-]*estate\b", r"\bнотариал\w*\b", r"\bnotar\w*\b", r"\bпрехвърл\w*\b"]),
    ("tax",            [r"\bнап\b", r"\bnap\b", r"\bнои\b", r"\bnoi\b", r"\btax\b", r"\bданък\b", r"\bданъчн\w*\b", r"\bvat\b", r"\bddc\b", r"\bдднк?\b"]),
    ("legal-compliance", [r"\bgdpr\b", r"\bnda\b", r"\bmsa\b", r"\bcompliance\b", r"\bюридическ\w*\b", r"\blegal\b", r"\bregulation\b", r"\bрегламент\w*\b", r"\bпроцедур\w*\b"]),
    ("accounting",     [r"\bсчетоводств\w*\b", r"\bсчетоводен\w*\b", r"\baccounting\b", r"\bbookkeeping\b", r"\bgodishen[\s_\-]*otchet\b", r"\bбаланс\w*\b", r"\bbalance[\s_\-]*sheet\b", r"\bp\&?l\b", r"\bпрофит[\s_\-]*loss\b"]),
    ("hr",             [r"\bhr\b", r"\bтрз\b", r"\bpayroll\b", r"\bзаплата\b", r"\bтрудов\w*\b", r"\bemployment\b", r"\bcv\b", r"\bautobiograf\w*\b"]),
    ("medical",        [r"\bmedical\b", r"\bлекар\w*\b", r"\bклиник\w*\b", r"\bclinic\b", r"\bhospital\b", r"\bлаборатори\w*\b", r"\blab[\s_\-]*result\w*\b", r"\brecept\w*\b"]),
    ("learning",       [r"\bcourse\b", r"\bкурс\w*\b", r"\btraining\b", r"\bобучение\b", r"\bworkshop\b", r"\bsemina\w*\b", r"\bсеминар\w*\b", r"\bcertificate\b", r"\bсертификат\w*\b"]),
    ("government",     [r"\bобщин\w*\b", r"\bmunicipality\b", r"\bмвр\b", r"\bmvr\b", r"\bgovernment\b", r"\bправителств\w*\b"]),
    ("delivery",       [r"\bекон[\s_\-]*т\b", r"\beconomic[\s_\-]*t\b", r"\bspeedy\b", r"\bспид\w*\b", r"\bdhl\b", r"\bдхл\b", r"\bdpd\b", r"\bдпд\b", r"\bcourier\b", r"\bкуриер\w*\b", r"\bdoставк\w*\b", r"\bdelivery[\s_\-]*note\b", r"\bтоваритъчн\w*\b"]),
    ("it",             [r"\bsoftware\b", r"\bсофтуер\w*\b", r"\bхардуер\w*\b", r"\bhardware\b", r"\blicense\b", r"\bлиценз\w*\b", r"\bsubscription\b", r"\bоblачн\w*\b", r"\baws\b", r"\bgcp\b", r"\bazure\b", r"\bgithub\b", r"\bjetbrains\b", r"\bjira\b", r"\batlassian\b"]),
    ("project-management", [r"\bproject[\s_\-]*management\b", r"\bproje?ktn[ои][\s_\-]*управление\b", r"\bpmp\b", r"\bscrum\b", r"\bagile\b"]),
    ("economics",      [r"\beconomic\w*\b", r"\bикономи\w*\b", r"\bportfolio\b", r"\bfinanc\w*\b", r"\bинвестиц\w*\b", r"\binvest\w*\b"]),
    ("mathematics",    [r"\bmath\w*\b", r"\bматемати\w*\b", r"\bgame[\s_\-]*theory\b", r"\bтеория[\s_\-]*на[\s_\-]*игрите\b", r"\bstatistic\w*\b", r"\bстатистик\w*\b", r"\bcalculus\b"]),
    ("sports",         [r"\bsport\w*\b", r"\bспорт\w*\b", r"\bfitness\b", r"\bфитнес\w*\b", r"\bgym\b", r"\bйога\w*\b", r"\byoga\b"]),
    ("politics",       [r"\bpolitic\w*\b", r"\bполити\w*\b", r"\bизбори\b", r"\belection\w*\b"]),
    ("literature",     [r"\bnovel\b", r"\broman\w*\b", r"\bроман\w*\b", r"\bpoetry\b", r"\bпоезия\b", r"\bbiograph\w*\b", r"\bбиограф\w*\b"]),
    ("health",         [r"\bhealth\b", r"\bздраве\w*\b", r"\bwellness\b", r"\bnutrition\b", r"\bхранене\w*\b"]),
    ("diy",            [r"\bdiy\b", r"\bдай\w*[\s_\-]*си\w*\b", r"\bдомашен[\s_\-]*майстор\w*\b", r"\bremont\w*\b", r"\bремонт\w*\b", r"\bbricolage\b", r"\bworkshop\b", r"\bwoodworking\b", r"\bplumbing\b", r"\bwiring\b", r"\bgardening\b", r"\bgrowing\b", r"\bfarm\b", r"\bhomestead\b", r"\brenovat\w*\b", r"\bdecorat\w*\b"]),
    ("home",           [r"\bhome\b", r"\bhouse\b", r"\bдомашн\w*\b", r"\bappliance\w*\b", r"\bкухн\w*\b", r"\bkitchen\b", r"\bbathroom\b", r"\bdecor\w*\b", r"\binterior\b"]),
    ("equipment",      [r"\bbosch\b", r"\bbosh\b", r"\bsiemens\b", r"\bwhirlpool\b", r"\baeg\b", r"\bdaikin\b", r"\bmitsubishi\b", r"\bvaillant\b", r"\bstiebel\b", r"\beltron\b", r"\bhoneywell\b", r"\b(lg|samsung|panasonic|sharp|hitachi)\b", r"\bgorenje\b", r"\bbeko\b", r"\bphilips\b", r"\bдвигател\w*\b", r"\bкомпрес\w*\b", r"\bкотлон?\b", r"\bboiler\b", r"\bкондиционер\w*\b"]),
    ("philosophy",     [r"\bphilosoph\w*\b", r"\bфилософ\w*\b", r"\bideology\b", r"\bcapitalism\b", r"\bsocialism\b", r"\bsocialist\b", r"\bcommunism\b", r"\bmarxis\w*\b", r"\bobjectiv\w*\b", r"\baustrian[\s_\-]*school\b"]),
    ("history",        [r"\bhistory\b", r"\bистори\w*\b", r"\bимпери\w*\b", r"\bempire\b", r"\bcrisi[se]\w*\b", r"\bcrash\b", r"\bturbulence\b", r"\bcollapse\b", r"\bevolution\b", r"\bcivilization\b", r"\bbiograph\w*\b", r"\bbiograf\w*\b"]),
    ("monetary",       [r"\bmonetar\w*\b", r"\bмонетарн\w*\b", r"\binflation\b", r"\binflaci\w*\b", r"\bcurrency\b", r"\bвалут\w*\b", r"\bgold\b", r"\bзлат\w*\b", r"\bdollar\b", r"\beuro\b", r"\bfederal[\s_\-]*reserve\b", r"\bcentral[\s_\-]*bank\b", r"\bgreenspan\b", r"\bfed\b", r"\binterest[\s_\-]*rate\w*\b", r"\binterestprices?\b", r"\bvelocity\b", r"\bquantity[\s_\-]*theory\b"]),
    ("management",     [r"\bmanagement\b", r"\bманаджмънт\b", r"\bmanager\b", r"\bстратеги\w*\b", r"\bstrateg\w*\b", r"\bleadership\b", r"\bлидерств\w*\b", r"\bcounsell?ing\b"]),
]

# Correspondent (organization) name → canonical full name.
# Used to suggest a Docspell Organization, not a tag.
CORRESPONDENT_PATTERNS: list[tuple[str, str]] = [
    (r"\ba1[\s\-_]*bulgaria\b", "A1 България"),
    (r"\ba1[\s\-_]*bg\b",        "A1 България"),
    (r"\bvivacom\b",             "Vivacom"),
    (r"\bвиваком\b",             "Vivacom"),
    (r"\byettel\b",              "Yettel"),
    (r"\bйетел\b",               "Yettel"),
    (r"\btelenor\b",             "Yettel"),     # legacy name
    (r"\bдск\b",                 "ДСК Банк"),
    (r"\bdsk\b",                 "ДСК Банк"),
    (r"\bunicredit\b",           "UniCredit Bulbank"),
    (r"\bуникредит\b",           "UniCredit Bulbank"),
    (r"\bбулбанк\b",             "UniCredit Bulbank"),
    (r"\bpostbank\b",            "Postbank"),
    (r"\bпощенска[\s_\-]*банк\w*\b", "Postbank"),
    (r"\bfibank\b",              "Fibank"),
    (r"\bпърва[\s_\-]*инвестиц\w*\b", "Fibank"),
    (r"\bобб\b",                 "ОББ"),
    (r"\bobb\b",                 "ОББ"),
    (r"\bprocredit\b",           "ProCredit Bank"),
    (r"\bпрокредит\b",           "ProCredit Bank"),
    (r"\ballianz\b",             "Allianz"),
    (r"\bалианц\b",              "Allianz"),
    (r"\braiffeisen\b",          "Raiffeisenbank"),
    (r"\bрайфайзен\b",           "Raiffeisenbank"),
    (r"\bevn\b",                 "EVN"),
    (r"\bевн\b",                 "EVN"),
    (r"\bcez\b",                 "ЧЕЗ"),
    (r"\bчез\b",                 "ЧЕЗ"),
    (r"\benergo[\s\-_]*pro\b",   "Energo-Pro"),
    (r"\bенерго[\s\-_]*про\b",   "Energo-Pro"),
    (r"\bтоплоф\w*\b",           "Топлофикация"),
    (r"\bsofia[\s\-_]*water\b",  "Софийска вода"),
    (r"\bсофийска[\s_\-]*вод\w*\b", "Софийска вода"),
    (r"\bvik\b",                 "ВиК"),
    (r"\bовергаз\b",             "Овергаз"),
    (r"\bовергаз\b",             "Овергаз"),
    (r"\bbulgargaz\b",           "Булгаргаз"),
    (r"\bнап\b",                 "НАП"),
    (r"\bnap\b",                 "НАП"),
    (r"\bнои\b",                 "НОИ"),
    (r"\bspeedy\b",              "Speedy"),
    (r"\bспид\w*\b",             "Speedy"),
    (r"\bекон[\s\-_]*т\b",       "Econt"),
    (r"\beconomic[\s\-_]*t\b",   "Econt"),
    (r"\bdhl\b",                 "DHL"),
    (r"\bдхл\b",                 "DHL"),
    (r"\bdpd\b",                 "DPD"),
    (r"\bjetbrains\b",           "JetBrains"),
    (r"\bgithub\b",              "GitHub"),
    (r"\batlassian\b",           "Atlassian"),
    (r"\bcanva\b",               "Canva"),
    (r"\bgoogle\b",              "Google"),
    (r"\bmicrosoft\b",           "Microsoft"),
    (r"\baws\b",                 "Amazon Web Services"),
]


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def normalize(text: str) -> str:
    """Lowercase, strip Latin diacritics, but keep Cyrillic intact."""
    if not text:
        return ""
    text = text.replace("_", " ").replace("-", " ").replace(".", " ")
    text = re.sub(r"\s+", " ", text).strip().lower()
    # Strip diacritics on Latin only (NFD then drop combining marks).
    normalized = []
    for ch in unicodedata.normalize("NFD", text):
        if unicodedata.category(ch) == "Mn":
            continue
        normalized.append(ch)
    return "".join(normalized)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


@dataclass
class Item:
    item_id: str
    title: str
    state: str = ""
    folder: str = ""
    current_tags: list[str] = field(default_factory=list)
    current_corr_org: str = ""
    direction: str = ""
    in_inbox: bool = False
    source_queries: set[str] = field(default_factory=set)


def load_items(searches_dir: Path) -> dict[str, Item]:
    items: dict[str, Item] = {}
    for path in sorted(searches_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"Skipping malformed JSON {path}: {exc}", file=sys.stderr)
            continue
        query = data.get("query", "")
        for raw in data.get("items", []) or []:
            iid = str(raw.get("id") or "")
            if not iid:
                continue
            existing = items.get(iid)
            if existing is None:
                existing = Item(
                    item_id=iid,
                    title=str(raw.get("title") or ""),
                    state=str(raw.get("state") or ""),
                    folder=str(raw.get("folder") or ""),
                    current_tags=[t for t in raw.get("tags", []) or []],
                    current_corr_org=str(raw.get("corrOrg") or ""),
                    direction=str(raw.get("direction") or ""),
                )
                items[iid] = existing
            existing.source_queries.add(query)
            if query == INBOX_QUERY:
                existing.in_inbox = True
    return items


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@dataclass
class Classification:
    doctype: str = ""              # single best doctype, or "" if none
    doctype_secondary: list[str] = field(default_factory=list)
    areas: list[str] = field(default_factory=list)
    correspondent: str = ""
    confidence: float = 0.0
    matched: list[str] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)


# Priority order: when multiple doctypes fire, the earlier one wins as primary.
DOCTYPE_PRIORITY = [
    "invoice", "receipt", "bank-statement", "tax", "contract",
    "insurance", "warranty", "payslip", "registration", "ticket",
    "id-document", "medical", "certificate", "report",
    "manual", "book", "presentation", "photo-scan", "legal", "cv",
]


# Patterns that very strongly suggest "library item" regardless of content
# keywords — based on common book-pirate sites and personal-library prefixes
# observed in this user's dataset.
BOOK_MARKERS: list[tuple[str, str]] = [
    (r"_oceanofpdf\.com_", "OceanofPDF marker"),
    (r"\bpdfdrive\b",      "PDFDrive marker"),
    (r"\bzlibrary\b",      "z-Library marker"),
    (r"\bлибген\b",        "LibGen marker (cyr)"),
    (r"\blibgen\b",        "LibGen marker"),
    (r"^aaa[\s_\-]",       "AAA sort prefix (library item)"),
    (r"^\d{6,}[\-_]",      "scribd-style numeric prefix"),
]

# Doctypes that are obviously NOT books — used to short-circuit the
# default-to-book fallback.
BUSINESS_DOCTYPES = {
    "invoice", "receipt", "bank-statement", "tax", "contract",
    "insurance", "warranty", "payslip", "registration", "ticket",
    "id-document", "medical", "cv",
}

DOCUMENT_EXT = re.compile(r"\.(pdf|epub|mobi|djvu|doc|docx|odt|txt|md|rtf|ppt|pptx)$", re.I)


def classify_title(title: str) -> Classification:
    norm = normalize(title)
    c = Classification()

    # Doctypes
    doctypes_hit: list[str] = []
    for name, patterns in DOCTYPE_PATTERNS:
        for pat in patterns:
            if re.search(pat, norm):
                doctypes_hit.append(name)
                c.matched.append(f"doctype:{name}({pat})")
                break
    # Dedupe while preserving priority order.
    if doctypes_hit:
        ordered = [d for d in DOCTYPE_PRIORITY if d in doctypes_hit]
        leftover = [d for d in doctypes_hit if d not in ordered]
        ordered.extend(leftover)
        c.doctype = ordered[0]
        c.doctype_secondary = ordered[1:]

    # Areas (many allowed)
    for name, patterns in AREA_PATTERNS:
        for pat in patterns:
            if re.search(pat, norm):
                if name not in c.areas:
                    c.areas.append(name)
                    c.matched.append(f"area:{name}({pat})")
                break

    # Correspondent (single best)
    for pat, canonical in CORRESPONDENT_PATTERNS:
        if re.search(pat, norm):
            c.correspondent = canonical
            c.matched.append(f"corr:{canonical}({pat})")
            break

    # Confidence — simple model:
    #   doctype + (area or correspondent)        → high (0.85)
    #   doctype only                              → medium (0.65)
    #   area or correspondent only                → low (0.45)
    #   nothing                                   → 0
    if c.doctype and (c.areas or c.correspondent):
        c.confidence = 0.85
    elif c.doctype:
        c.confidence = 0.65
    elif c.correspondent:
        c.confidence = 0.55
    elif c.areas:
        c.confidence = 0.40
    else:
        c.confidence = 0.0

    # Filename-shape penalties / hints
    if re.fullmatch(r"[a-z0-9]{6,}", norm) and not c.matched:
        c.reasoning.append("title looks like a random/hash filename")
    if len(norm) <= 4:
        c.confidence *= 0.5
        c.reasoning.append("very short title — low signal")

    # ---- DEFAULT-TO-BOOK FALLBACK ----------------------------------------
    # If no business doctype matched and the title looks like a document file,
    # treat it as library / reference material. This collection is heavily
    # book-skewed (~95%+), so the default for an unknown-but-document-shaped
    # title is "library item", not "untriaged".
    is_business = c.doctype in BUSINESS_DOCTYPES
    if not is_business:
        # Strong library markers → upgrade to high confidence.
        for pat, reason in BOOK_MARKERS:
            if re.search(pat, norm):
                if not c.doctype:
                    c.doctype = "book"
                    c.matched.append(f"doctype:book({reason})")
                c.confidence = max(c.confidence, 0.90)
                c.reasoning.append(reason)
                break
        # No marker, but document-shaped filename with a long-ish title →
        # medium-high confidence library default.
        if not c.doctype and DOCUMENT_EXT.search(title or "") and len(norm) >= 12:
            word_count = len(re.findall(r"\b[\w']{2,}\b", norm))
            if word_count >= 2:
                c.doctype = "book"
                c.matched.append("doctype:book(document filename, long-form title)")
                c.confidence = max(c.confidence, 0.70)
                c.reasoning.append("default-to-book: document filename, multi-word title")
        # Short/Latin-name files (e.g. Alesina.docx, calculation.pdf) — likely
        # library but lower confidence.
        if not c.doctype and DOCUMENT_EXT.search(title or "") and len(norm) >= 5:
            c.doctype = "book"
            c.matched.append("doctype:book(short document filename)")
            c.confidence = max(c.confidence, 0.55)
            c.reasoning.append("default-to-book: short document filename")

    return c


# ---------------------------------------------------------------------------
# Folder assignment
# ---------------------------------------------------------------------------


# Doctypes that almost certainly belong to Company workflow vs. Personal.
COMPANY_DOCTYPES = {"invoice", "receipt", "bank-statement", "contract", "payslip", "report"}
PERSONAL_DOCTYPES = {"id-document", "medical", "cv", "ticket", "certificate"}
ARCHIVE_DOCTYPES = {"book", "manual", "presentation"}

# Doctypes that are ambiguous and rely on areas to disambiguate.
AMBIGUOUS_DOCTYPES = {"insurance", "warranty", "tax", "legal", "registration", "photo-scan"}

# Area-only fallbacks if no doctype.
COMPANY_AREAS = {"accounting", "hr", "it", "project-management"}
PERSONAL_AREAS = {"medical", "car", "property", "telecom", "utility-power", "utility-heat", "utility-water", "utility-gas", "internet", "tax", "government"}
ARCHIVE_AREAS = {
    "learning", "economics", "mathematics", "sports", "politics",
    "literature", "health", "diy", "home", "equipment",
    "philosophy", "history", "monetary", "management",
}


def assign_folder(c: Classification) -> tuple[str, list[str]]:
    """Return (folder, reasoning_bits)."""
    reasoning: list[str] = []
    if c.doctype in COMPANY_DOCTYPES:
        return "Company", [f"doctype={c.doctype} is company-facing"]
    if c.doctype in PERSONAL_DOCTYPES:
        return "Personal", [f"doctype={c.doctype} is personal"]
    if c.doctype in ARCHIVE_DOCTYPES:
        return "Archive", [f"doctype={c.doctype} is reference material"]

    if c.doctype in AMBIGUOUS_DOCTYPES:
        # Disambiguate by area
        if any(a in COMPANY_AREAS for a in c.areas):
            return "Company", [f"doctype={c.doctype} + company-area={c.areas}"]
        if any(a in PERSONAL_AREAS for a in c.areas):
            return "Personal", [f"doctype={c.doctype} + personal-area={c.areas}"]
        return "", [f"doctype={c.doctype} but ambiguous area — needs review"]

    # No doctype — use areas alone
    if any(a in ARCHIVE_AREAS for a in c.areas):
        return "Archive", [f"area={c.areas} suggests reference material"]
    if any(a in COMPANY_AREAS for a in c.areas):
        return "Company", [f"area={c.areas} suggests company workflow"]
    if any(a in PERSONAL_AREAS for a in c.areas):
        return "Personal", [f"area={c.areas} suggests personal workflow"]
    return "", ["no folder signal from title alone"]


def decision_for(c: Classification, folder: str) -> str:
    """Decide whether the suggestion is safe to auto-apply.

    Archive is the safe "library" default — for a dataset that's
    overwhelmingly reference material, accepting Archive at a lower
    confidence is fine. Personal/Company/Clients are more consequential
    folders and require high confidence.
    """
    if not folder:
        return "needs_review" if c.matched else "unclassified"
    if folder == "Archive" and c.confidence >= 0.65:
        return "classified"
    if c.confidence >= 0.80:
        return "classified"
    if c.confidence >= 0.55:
        return "needs_review"
    return "needs_review"


def encoded_tags(c: Classification) -> list[str]:
    """Return tags in 'category:name' shorthand for the CSV."""
    tags: list[str] = []
    if c.doctype:
        tags.append(f"doctype:{c.doctype}")
    for d in c.doctype_secondary:
        tags.append(f"doctype:{d}")
    for area in c.areas:
        tags.append(f"area:{area}")
    return tags


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def write_csv(items: dict[str, Item], out_path: Path) -> dict[str, int]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = defaultdict(int)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "item_id",
                "title",
                "in_inbox",
                "current_folder",
                "current_tags",
                "current_corr_org",
                "review_decision",
                "suggested_folder",
                "safe_suggested_folder",   # for apply_reviewed_actions.py compatibility
                "suggested_doctype",
                "suggested_areas",
                "suggested_correspondent",
                "safe_add_tags",           # for apply_reviewed_actions.py compatibility
                "confidence",
                "matched_keywords",
                "reasoning",
                "source_queries",
            ],
        )
        writer.writeheader()
        for item in sorted(items.values(), key=lambda i: i.title.casefold()):
            c = classify_title(item.title)
            folder, folder_reasoning = assign_folder(c)
            decision = decision_for(c, folder)
            counts[decision] += 1
            counts[f"folder={folder or 'unset'}"] += 1
            tags = encoded_tags(c)
            # safe_* is populated ONLY for high-confidence rows, so apply
            # script will only act on those by default.
            safe_folder = folder if decision == "classified" else ""
            safe_tags = ";".join(tags) if decision == "classified" else ""
            writer.writerow(
                {
                    "item_id": item.item_id,
                    "title": item.title,
                    "in_inbox": "yes" if item.in_inbox else "",
                    "current_folder": item.folder,
                    "current_tags": ";".join(item.current_tags),
                    "current_corr_org": item.current_corr_org,
                    "review_decision": decision,
                    "suggested_folder": folder,
                    "safe_suggested_folder": safe_folder,
                    "suggested_doctype": c.doctype,
                    "suggested_areas": ";".join(c.areas),
                    "suggested_correspondent": c.correspondent,
                    "safe_add_tags": safe_tags,
                    "confidence": f"{c.confidence:.2f}",
                    "matched_keywords": "; ".join(c.matched),
                    "reasoning": "; ".join(folder_reasoning + c.reasoning),
                    "source_queries": ";".join(sorted(item.source_queries)),
                }
            )
    return counts


def main() -> int:
    if not SEARCHES_DIR.exists():
        print(f"Searches dir not found: {SEARCHES_DIR}", file=sys.stderr)
        return 1
    items = load_items(SEARCHES_DIR)
    print(f"Loaded {len(items)} unique items from {SEARCHES_DIR}")

    counts = write_csv(items, OUTPUT_CSV)

    print(f"Wrote {OUTPUT_CSV}")
    print()
    print("Decision distribution:")
    for key in ("classified", "needs_review", "unclassified"):
        print(f"  {key:14s}: {counts.get(key, 0)}")
    print()
    print("Folder distribution:")
    for folder in ("Personal", "Company", "Clients", "Archive", "unset"):
        key = f"folder={folder}"
        print(f"  {folder:14s}: {counts.get(key, 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
