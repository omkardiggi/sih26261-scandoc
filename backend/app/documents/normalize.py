"""Normalisation helpers for extracted values (dates, academic years, identifiers, names)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from rapidfuzz import fuzz

MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",),
            ("jun", "june"), ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"),
            ("oct", "october"), ("nov", "november"), ("dec", "december"),
        ],
        start=1,
    )
    for m in names
}

_MONTH_RE = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"

DATE_PATTERNS = [
    # 16-03-2026, 16/03/2026, 16.03.2026 (Indian day-first convention)
    (re.compile(r"\b(\d{1,2})[\-/\.](\d{1,2})[\-/\.]((?:19|20)\d{2})\b"), "dmy"),
    # 2026-03-16
    (re.compile(r"\b((?:19|20)\d{2})-(\d{1,2})-(\d{1,2})\b"), "ymd"),
    # 16-Mar-2026, 16 March 2026, 16th March, 2026
    (re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?[\s\-\.,]*{_MONTH_RE}[\s\-\.,]*((?:19|20)\d{{2}})\b", re.I), "dMy"),
    # March 16, 2026
    (re.compile(rf"\b{_MONTH_RE}[\s\.]+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+((?:19|20)\d{{2}})\b", re.I), "Mdy"),
]

DATE_ANY_RE = re.compile("|".join(p.pattern for p, _ in DATE_PATTERNS), re.I)


def clean_text(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip()


def parse_date(text: str | None) -> tuple[date | None, str | None]:
    """Return (date, matched_text) for the first unambiguous date in text."""
    if not text:
        return None, None
    t = clean_text(text)
    for rx, kind in DATE_PATTERNS:
        m = rx.search(t)
        if not m:
            continue
        try:
            if kind == "dmy":
                d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            elif kind == "ymd":
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            elif kind == "dMy":
                d, mo, y = int(m.group(1)), MONTHS[m.group(2).lower()[:3]], int(m.group(3))
            else:
                mo, d, y = MONTHS[m.group(1).lower()[:3]], int(m.group(2)), int(m.group(3))
            return date(y, mo, d), m.group(0)
        except (ValueError, KeyError):
            continue
    return None, None


AY_RE = re.compile(r"\b((?:19|20)\d{2})\s*[\-–/]\s*((?:19|20)?\d{2})\b")


def normalize_academic_year(text: str | None) -> tuple[str | None, str | None, bool]:
    """Return (normalised 'YYYY-YY', matched text, is_consecutive)."""
    if not text:
        return None, None, False
    m = AY_RE.search(clean_text(text))
    if not m:
        return None, None, False
    start = int(m.group(1))
    end_raw = m.group(2)
    end = int(end_raw) if len(end_raw) == 4 else (start // 100) * 100 + int(end_raw)
    consecutive = end == start + 1
    return f"{start}-{str(start + 1)[-2:]}" if consecutive else f"{start}-{str(end)[-2:]}", m.group(0), consecutive


AICTE_ID_RE = re.compile(r"\b1-\d{6,12}\b")


def normalize_aicte_id(text: str | None) -> str | None:
    if not text:
        return None
    t = clean_text(text).replace(" ", "")
    t = t.replace("–", "-").replace("—", "-")
    m = AICTE_ID_RE.search(t)
    return m.group(0) if m else None


_ABBREV = {
    "ENGG": "ENGINEERING",
    "ENGG.": "ENGINEERING",
    "INST": "INSTITUTE",
    "INST.": "INSTITUTE",
    "TECH": "TECHNOLOGY",
    "TECH.": "TECHNOLOGY",
    "MGMT": "MANAGEMENT",
    "COLL": "COLLEGE",
    "UNIV": "UNIVERSITY",
    "&": "AND",
    "DEPT": "DEPARTMENT",
}


def normalize_name(text: str | None) -> str:
    if not text:
        return ""
    t = clean_text(text).upper()
    t = t.replace("&", " AND ")
    t = re.sub(r"[^A-Z0-9 ]+", " ", t)
    words = [_ABBREV.get(w, w) for w in t.split()]
    words = [w for w in words if w not in {"THE", "OF"}]
    return " ".join(words)


def name_similarity(a: str | None, b: str | None) -> float:
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    return float(fuzz.token_sort_ratio(na, nb))


# Spellings of the same word that institution names use interchangeably (compared after upper-casing).
_VARIANTS = {"SHREE": "SRI", "SHRI": "SRI", "SREE": "SRI", "SHRE": "SRI", "SRI": "SRI", "SMT": "SHRIMATI", "SHRIMATHI": "SHRIMATI",
             "SAHEB": "SAHEB", "SAHAB": "SAHEB", "MAHAVIDYALAYA": "COLLEGE", "VIDYALAYA": "VIDYALAYA", "CENTRE": "CENTER",
             "PG": "POSTGRADUATE", "TECHNOLOGICAL": "TECHNOLOGY", "ENGINEERING": "ENGINEERING", "ENGINEER": "ENGINEERING"}
# Words every institution shares: a difference in these alone does not mean another institution.
_GENERIC = {"INSTITUTE", "INSTITUTION", "TECHNOLOGY", "COLLEGE", "ENGINEERING", "UNIVERSITY", "MANAGEMENT", "SCIENCE", "SCIENCES",
            "DEPARTMENT", "SCHOOL", "POLYTECHNIC", "GOVERNMENT", "GOVT", "RESEARCH", "STUDIES", "EDUCATION", "ACADEMY", "CENTER",
            "POSTGRADUATE", "PHARMACY", "ARCHITECTURE", "TECHNICAL", "ARTS", "COMMERCE", "APPLIED", "ADVANCED", "STUDY", "WOMEN",
            "WOMENS", "SRI", "AUTONOMOUS", "DEEMED", "CAMPUS", "TRUST", "SOCIETY", "COMPUTER", "COMPUTERS", "INFORMATION", "MEDICAL", "HOSPITAL"}
_NAME_NOISE = {"THE", "OF", "AND", "FOR", "IN", "AT", "M", "S", "MS", "PVT", "LTD", "PRIVATE", "LIMITED", "DR"}
_ADDRESS_HINT = re.compile(r"\d|\b(ROAD|RD|STREET|ST|NAGAR|MARG|PO|POST|DIST|DISTRICT|TALUK|TQ|NEAR|PIN|SECTOR|PLOT|STATION|LANE|CROSS|MAIN|HIGHWAY|CAMPUS)\b")


def _name_words(text: str | None) -> list[str]:
    # Runs of single letters are initials: "R V K" -> "RVK", so "R.V." matches "RV".
    words = re.sub(r"\b([A-Z])\b(?:\s+(?=[A-Z]\b))?", r"\1", normalize_name(text)).split()
    return [_VARIANTS.get(w, w) for w in words if w not in _NAME_NOISE]


def core_name(name: str | None, places: tuple[str | None, ...] = ()) -> str:
    """The institution's name without an address that was typed into the same field ("X COLLEGE, STATION ROAD - 431005")."""
    parts = [p.strip() for p in re.split(r",|\s-\s|\(", clean_text(name or "")) if p.strip()]
    if not parts:
        return ""
    place_words = {w for p in places if p for w in normalize_name(p).split()}
    keep = [parts[0]]
    for p in parts[1:]:
        words = set(normalize_name(p).split())
        if _ADDRESS_HINT.search(p.upper()) or (words and words <= place_words):
            break  # the rest is address
        keep.append(p)
    return ", ".join(keep)


def _word_match(want: str, have: list[str], compact: str) -> str | None:
    """How a registry word appears in the document: exact, as an OCR/spelling variant, or written together with the next word."""
    if want in have:
        return "exact"
    if len(want) >= 5 and any(len(h) >= 4 and fuzz.ratio(want, h) >= (78 if len(want) >= 8 else 84) for h in have):
        return "close"  # OCR misreads such as ENGIHEERTNG
    if len(want) >= 4 and any("".join(have[i:j]) == want for i in range(len(have)) for j in range(i + 2, min(i + 4, len(have) + 1))):
        return "joined"  # RAMDEOBABA vs RAMDEO BABA
    return None


@dataclass
class NameComparison:
    status: str  # pass | warning | fail
    score: float
    document_name: str
    registry_name: str
    matched: list[str]
    missing: list[str]
    extra: list[str]
    notes: list[str]
    reason: str


def compare_names(document_name: str, registry_name: str, *, places: tuple[str | None, ...] = (), allow_extra: bool = False) -> NameComparison:
    """Compare two institution names word by word, ignoring case, punctuation, spelling variants and small OCR errors.

    ``allow_extra``: the document text may contain more than the name (a whole line of text), so extra words are not held against it.
    """
    reg = core_name(registry_name, places) or registry_name
    want = list(dict.fromkeys(_name_words(reg)))
    have = _name_words(document_name)
    compact = "".join(have)
    place_words = {_VARIANTS.get(w, w) for p in places if p for w in normalize_name(p).split()}
    matched, missing, notes, joined = [], [], [], []
    for w in want:
        how = _word_match(w, have, compact)
        if how:
            matched.append(w)
            if how == "joined":
                joined.append(w)
            if how != "exact":
                notes.append(f"{w} read as a {'misspelt/OCR variant' if how == 'close' else 'joined word'}")
        elif w in place_words:
            continue  # a city/district in the record's name need not be printed
        else:
            missing.append(w)
    used = set(matched)
    extra = [] if allow_extra else [h for h in dict.fromkeys(have) if h not in used and h not in place_words
                                    and not any(fuzz.ratio(h, m) >= 78 for m in matched) and not any(h in j for j in joined)]
    honorific = r"(?i)\b(shree|shri|sree|sri|shre)\b"
    dv, rv = re.findall(honorific, document_name or ""), re.findall(honorific, reg)
    if dv and rv and dv[0].upper() != rv[0].upper():
        notes.append(f"'{dv[0]}' and '{rv[0]}' treated as the same word")
    total = len(matched) + len(missing)
    cover = len(matched) / total if total else 0.0
    score = round(100 * cover * (1 - min(0.5, 0.1 * len(extra))), 1)
    distinct_diff = [w for w in missing + extra if w not in _GENERIC]
    if not missing and not extra:
        status = "pass"
    elif distinct_diff and not (allow_extra and not missing):
        status = "fail"  # a different proper name (e.g. AGRASEN vs SURAJMAL) means a different institution
    elif cover >= 0.6 and len(missing) + len(extra) <= 3:
        status = "warning"
    else:
        status = "fail"
    if status == "pass":
        reason = "Names match" + (" (ignoring letter case and punctuation)" if document_name.strip() != reg.strip() else "") + "."
    else:
        bits = [f"The document names '{clean_text(document_name)}' but the institution record is '{reg}'."]
        if missing:
            bits.append("Missing from the document: " + ", ".join(missing) + ".")
        if extra:
            bits.append("In the document instead: " + ", ".join(extra) + ".")
        if matched:
            bits.append("Common words: " + ", ".join(matched) + ".")
        if status == "warning":
            bits.append("Only general words differ (a short form or a reading error); please confirm.")
        elif distinct_diff:
            bits.append(f"The distinctive part of the name differs ({', '.join(distinct_diff[:4])}), so this looks like a different institution's document.")
        else:
            bits.append("Too much of the name differs; this looks like a different institution's document.")
        reason = " ".join(bits)
    if notes:
        reason += " Note: " + "; ".join(dict.fromkeys(notes)) + "."
    return NameComparison(status, score, clean_text(document_name), reg, matched, missing, extra, list(dict.fromkeys(notes)), reason)


_INSTITUTION_WORD = re.compile(r"(?i)(college|institute|institution|university|school|polytechnic|academy|vidyalaya|mahavidyalaya)")


def _institution_named(lines: list[str]) -> str | None:
    """The first line that reads like some institution's name (used to say whose document this appears to be)."""
    for ln in lines:
        span = _name_span(ln)
        if (_INSTITUTION_WORD.search(span) or re.match(r"(?i)^\s*m\s*/\s*s", ln)) and 3 <= len(_name_words(span)) <= 10 and len(span) <= 90:
            return span
    return None


def find_name_in_text(text: str, registry_name: str, places: tuple[str | None, ...] = ()) -> NameComparison:
    """Find the line of the document that best matches the institution's name and compare it."""
    lines = [ln.strip() for ln in (text or "").splitlines() if len(ln.strip()) >= 4]
    reg = core_name(registry_name, places) or registry_name
    want = list(dict.fromkeys(_name_words(reg)))
    distinct = [w for w in want if w not in _GENERIC]
    best, best_key = None, None
    for i, ln in enumerate(lines[:3000]):
        for cand in (ln, f"{ln} {lines[i + 1]}" if i + 1 < len(lines) else ln):  # names wrapped onto two lines
            words = _name_words(cand)
            if not words or len(words) > 3 * max(1, len(want)) + 4:
                continue
            found = [w for w in want if _word_match(w, words, "".join(words))]
            key = (sum(w in distinct for w in found), len(found), -len(words))
            if found and (best_key is None or key > best_key):
                best, best_key = cand, key
    other = _institution_named(lines)
    if best is None or (distinct and best_key[0] == 0):
        # Not even the distinctive part of the name (e.g. RAMDEOBABA, COCHIN) is printed anywhere.
        what = f"the distinctive words of the name ({', '.join(distinct)})" if distinct and best is not None else "the institution's name"
        reason = f"The document does not mention {what}; the institution record is '{reg}'."
        if other:
            reason += f" The document names '{other}' instead."
        reason += " This looks like a different institution's document."
        return NameComparison("fail", 0.0, other or "", reg, [], distinct or want, [], [], reason)
    # Compare the line as a whole: other words on the same line are not held against it.
    res = compare_names(best, registry_name, places=places, allow_extra=True)
    if res.status == "pass":
        return res
    # Judge the name-like part of the line strictly: other proper names in it (SREE RAMA vs GOVERNMENT) count against it.
    strict = compare_names(_name_span(best), registry_name, places=places)
    if strict.status == "fail" and other and normalize_name(other) != normalize_name(strict.document_name):
        strict.reason += f" The document appears to belong to '{other}'."
    return strict


def _name_span(line: str) -> str:
    """The part of a line that looks like an institution name (drop 'M/S', 'located at …', addresses)."""
    t = re.sub(r"(?i)^\s*(m\s*/\s*s\.?|name of (the )?(institution|company|establishment)[^:]*:)\s*", "", clean_text(line))
    t = re.split(r"(?i),|\blocated\b|\bsituated\b|\bat\s+sy\b|\(|\bis\b|\bhas\b", t)[0]
    return t.strip()


def parse_int(text: str | None) -> int | None:
    if text is None:
        return None
    t = re.sub(r"[*#†‡^]+$", "", clean_text(str(text)).replace(",", "")).strip()  # footnote markers, e.g. "9**"
    m = re.fullmatch(r"\d{1,7}", t)
    return int(m.group(0)) if m else None


def digits_tokens(text: str) -> set[str]:
    return set(re.findall(r"\d[\d,\.\-/]*\d|\d", text or ""))


def word_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", clean_text(text).lower())
