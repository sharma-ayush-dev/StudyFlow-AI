"""
text_extractor.py — Document parsing + LLM extraction pipeline.

Key changes vs original:
  - Adaptive max_tokens for extraction (based on content size)
  - Hard text-size limits before LLM call (abuse protection)
  - Compact JSON sanitisation preserved
  - Normalisation logic unchanged
"""

import os
import re
import json
import base64
import datetime
from pypdf import PdfReader
from docx import Document
from openai import OpenAI
import apikey


client = OpenAI(base_url="https://api.doubleword.ai/v1", api_key=apikey.key)

VISION_MODELS = [
    "openai/gpt-oss-20b"
]

# ── EXTRACTION LIMITS ─────────────────────────────────────────────────────────
# Characters of raw text sent to the model (per file). Beyond this we truncate.
MAX_TEXT_CHARS_PER_FILE = 12_000   # ~3000 tokens of text context per file
MAX_TOTAL_TEXT_CHARS    = 20_000   # aggregate cap across all files + manual text

# Token budget for extraction output. The schema is compact; 1500 tokens
# comfortably covers 6 subjects × 7 topics × 4 subtopics.
EXTRACT_MIN_TOKENS  = 2000   # floor: 3 subjects × 5 topics easily exceeds 800 tokens
EXTRACT_MAX_TOKENS  = 4000   # ceiling: generous enough for large syllabi
EXTRACT_BUFFER_MULT = 1.4


# ── DATE UTILS ───────────────────────────────────────────────────────────────

def _parse_date(s: str) -> datetime.date:
    d, m, y = s.strip().split('-')
    return datetime.date(int(y), int(m), int(d))

def _format_date(d: datetime.date) -> str:
    return d.strftime('%d-%m-%Y')

def _date_range(start: str, end: str) -> dict:
    cur, stop = _parse_date(start), _parse_date(end)
    out = {}
    while cur <= stop:
        out[_format_date(cur)] = 'none'
        cur += datetime.timedelta(days=1)
    return out


# ── NORMALISATION ────────────────────────────────────────────────────────────

def _find(d: dict, *keys):
    for k in keys:
        if k in d: return d[k]
    lo = {x.lower(): d[x] for x in d}
    for k in keys:
        if k.lower() in lo: return lo[k.lower()]
    return None


def _normalize(payload) -> dict:
    """
    Normalises LLM output into:
    {
      "Exam_dates": { "Subject": "DD-MM-YYYY" },
      "Subjects": {
        "Subject": {
          "TopicName": {
            "status": "none",
            "subtopics": ["Subtopic 1", "Subtopic 2"]
          }
        }
      },
      "study_days": {}
    }
    """
    if not isinstance(payload, dict):
        return {'Exam_dates': {}, 'Subjects': {}, 'study_days': {}}

    raw_s = _find(payload, 'Subjects', 'subjects') or {}
    raw_d = _find(payload, 'Exam_dates', 'exam_dates', 'ExamDates', 'examDates') or {}
    ns, nd = {}, {}

    def _norm_topic(val) -> dict:
        if isinstance(val, dict):
            subs = val.get('subtopics') or []
            if not isinstance(subs, list):
                subs = []
            subs = [str(s).strip() for s in subs if str(s).strip()]
            return {'status': 'none', 'subtopics': subs}
        return {'status': 'none', 'subtopics': []}

    if isinstance(raw_s, list):
        for s in raw_s:
            if not isinstance(s, dict): continue
            name = str(s.get('subject') or s.get('name') or '').strip()
            if not name: continue
            topics_raw = s.get('topics') or {}
            if isinstance(topics_raw, list):
                topics = {str(t).strip(): {'status': 'none', 'subtopics': []}
                          for t in topics_raw if isinstance(t, str) and t.strip()}
            elif isinstance(topics_raw, dict):
                topics = {str(k).strip(): _norm_topic(v)
                          for k, v in topics_raw.items() if str(k).strip()}
            else:
                topics = {}
            if topics: ns[name] = topics
            ed = s.get('exam_date') or s.get('examDate')
            if ed and str(ed).strip() not in ('null', 'None', ''):
                nd[name] = str(ed).strip()
        if isinstance(raw_d, dict):
            for k, v in raw_d.items():
                if v and str(v).strip() not in ('null', 'None', ''):
                    nd[str(k).strip()] = str(v).strip()

    elif isinstance(raw_s, dict):
        for subj, topics_raw in raw_s.items():
            subj = str(subj).strip()
            if not subj: continue
            if isinstance(topics_raw, dict):
                norm_topics = {}
                for tname, tval in topics_raw.items():
                    tname = str(tname).strip()
                    if not tname: continue
                    norm_topics[tname] = _norm_topic(tval)
                if norm_topics: ns[subj] = norm_topics
        if isinstance(raw_d, dict):
            for subj, date in raw_d.items():
                if date and str(date).strip() not in ('null', 'None', ''):
                    nd[str(subj).strip()] = str(date).strip()

    return {'Exam_dates': nd, 'Subjects': ns, 'study_days': {}}


def _filter_dates(payload: dict) -> dict:
    subjects = payload.get('Subjects') or {}
    payload['Exam_dates'] = {s: d for s, d in (payload.get('Exam_dates') or {}).items()
                              if subjects.get(s)}
    return payload


def _ensure_study_days(payload: dict, today_str: str) -> dict:
    valid = []
    for d in (payload.get('Exam_dates') or {}).values():
        try: valid.append(_parse_date(d))
        except: pass
    payload['study_days'] = (
        _date_range(today_str, _format_date(max(valid))) if valid else {}
    )
    return payload


# ── JSON SANITISATION ────────────────────────────────────────────────────────

def _sanitize_json_str(s: str) -> str:
    """Replace Unicode lookalike characters and invisible chars that break json.loads()."""
    # Smart quotes and typographic apostrophes
    s = s.replace('\u2018', "'").replace('\u2019', "'")
    s = s.replace('\u201c', '"').replace('\u201d', '"')
    s = s.replace('\u201a', "'").replace('\u201b', "'")
    s = s.replace('\u201e', '"').replace('\u201f', '"')
    # Typographic dashes
    s = s.replace('\u2013', '-').replace('\u2014', '-')
    # Spaces / zero-width chars (replace with plain space or remove)
    s = s.replace('\u00a0', ' ')   # non-breaking space
    s = s.replace('\u00ad', '')    # soft hyphen
    s = s.replace('\u200b', '')    # zero-width space
    s = s.replace('\u200c', '')    # zero-width non-joiner
    s = s.replace('\u200d', '')    # zero-width joiner
    s = s.replace('\u2060', '')    # word joiner
    s = s.replace('\ufeff', '')    # BOM (anywhere in string)
    # Trailing commas before } or ]
    s = re.sub(r',\s*([}\]])', r'\1', s)
    # --- Comprehensive sweep: strip ALL remaining invisible / control characters ---
    # C0 controls (except \t=0x09, \n=0x0A, \r=0x0D which are valid in JSON strings)
    s = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', s)
    # C1 controls (0x80-0x9F) — never valid unescaped in JSON
    s = re.sub(r'[\x80-\x9f]', '', s)
    # Unicode general punctuation / invisible separators
    # U+2000-U+200F: various spaces and invisible formatting chars
    # U+2028: line separator, U+2029: paragraph separator — both break JSON parsers
    # U+202A-U+202F: directional / narrow no-break space
    # U+2061-U+2064: invisible math operators
    s = re.sub(r'[\u2000-\u200f\u2028-\u202f\u2061-\u2064]', '', s)
    return s


def _extract_json(text: str) -> dict:
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'```(?:json)?', '', text).strip()
    text = _sanitize_json_str(text)

    start = text.find('{')
    end   = text.rfind('}')

    if start == -1 or end == -1:
        raise ValueError("No JSON object found in model output")

    candidate = text[start:end+1]

    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        print("RAW LLM OUTPUT:\n", text)
        print("BROKEN JSON:\n", candidate)
        raise e


# ── FILE READERS ─────────────────────────────────────────────────────────────

def extract_pdf(path: str) -> str:
    return '\n'.join(p.extract_text() or '' for p in PdfReader(path).pages)

def extract_docx(path: str) -> str:
    return '\n'.join(p.text for p in Document(path).paragraphs)

def extract(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == '.pdf':  return extract_pdf(path)
    if ext == '.docx': return extract_docx(path)
    raise Exception(f'extract() does not support {ext}')


def _trim_text(text: str, max_chars: int) -> str:
    """Truncate text to max_chars, appending a note if trimmed."""
    if len(text) <= max_chars:
        return text
    # Try to break at a paragraph boundary near the limit
    cut = text[:max_chars].rfind('\n')
    if cut < max_chars * 0.8:
        cut = max_chars
    return text[:cut] + '\n[...document truncated for processing...]'


def _file_parts(path: str, label: str) -> list:
    ext   = os.path.splitext(path)[1].lower()
    parts = [{'type': 'text', 'text': f'\n---{label}---\n'}]
    if ext in ('.png', '.jpg', '.jpeg', '.webp'):
        with open(path, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode()
        mime = 'image/png' if ext == '.png' else 'image/jpeg'
        parts.append({'type': 'image_url',
                      'image_url': {'url': f'data:{mime};base64,{b64}'}})
    elif ext == '.pdf':
        raw  = extract_pdf(path) or '(empty)'
        parts.append({'type': 'text', 'text': _trim_text(raw, MAX_TEXT_CHARS_PER_FILE)})
    elif ext == '.docx':
        raw  = extract_docx(path) or '(empty)'
        parts.append({'type': 'text', 'text': _trim_text(raw, MAX_TEXT_CHARS_PER_FILE)})
    else:
        raise Exception(f'Unsupported: {ext}')
    return parts


# ── ADAPTIVE TOKEN BUDGET ────────────────────────────────────────────────────

def _estimate_extraction_tokens(content_char_count: int) -> int:
    """
    Estimate output tokens for extraction based on input content size.
    Output schema is compact; 1 subject ≈ 200 output tokens.
    We expect roughly 1 subject per 2000 input chars.
    """
    est_subjects = max(1, content_char_count // 2000)
    # Each subject: ~8 topics × ~30 tokens each = ~240 tokens + overhead
    est_output   = est_subjects * 300
    return int(est_output * EXTRACT_BUFFER_MULT)


# ── PROMPT ───────────────────────────────────────────────────────────────────

_PROMPT = """Extract study planner data from the SYLLABUS and DATESHEET provided.
You MUST return valid JSON. No explanations, no markdown, no trailing commas, no comments.

{
  "Exam_dates": {"SubjectName": "DD-MM-YYYY"},
  "Subjects": {
    "SubjectName": {
      "TopicName": {
        "subtopics": ["Subtopic 1", "Subtopic 2", "Subtopic 3"]
      }
    }
  }
}

Rules:
- Only include subjects present in SYLLABUS
- 3-7 topics per subject (unit/chapter titles preferred)
- 2-5 subtopics per topic (key concepts within that unit)
- If no subtopics can be identified, use an empty array []
- Exam dates in DD-MM-YYYY format; omit subject from Exam_dates if not found"""


# ── MAIN ─────────────────────────────────────────────────────────────────────

def organize_with_llm(file_paths: list,
                      manual_text: str = None,
                      today_str:   str = None,
                      model_list:  list = None) -> dict:
    if today_str  is None: today_str  = datetime.date.today().strftime('%d-%m-%Y')
    if model_list is None: model_list = VISION_MODELS
    if not file_paths and not manual_text:
        raise ValueError('No input provided')

    content = []
    total_text_chars = 0

    if file_paths:
        for i, p in enumerate(file_paths[:-1]):
            parts = _file_parts(p, f'SYLLABUS {i+1}')
            content.extend(parts)
            for part in parts:
                if part.get('type') == 'text':
                    total_text_chars += len(part.get('text', ''))

        parts = _file_parts(file_paths[-1], 'DATESHEET')
        content.extend(parts)
        for part in parts:
            if part.get('type') == 'text':
                total_text_chars += len(part.get('text', ''))

    if manual_text:
        trimmed = _trim_text(manual_text, MAX_TEXT_CHARS_PER_FILE)
        content.append({'type': 'text',
                        'text': f'\n---MANUAL SYLLABUS---\n{trimmed}\n'})
        total_text_chars += len(trimmed)

    # Global text-size guard (defense-in-depth)
    if total_text_chars > MAX_TOTAL_TEXT_CHARS:
        print(f'[EXTRACT] Total text {total_text_chars} chars — already trimmed per-file, '
              f'proceeding with truncated content.')

    content.append({'type': 'text', 'text': _PROMPT})

    # Adaptive token budget for extraction output
    extract_max_tokens = max(
        EXTRACT_MIN_TOKENS,
        min(_estimate_extraction_tokens(total_text_chars), EXTRACT_MAX_TOKENS)
    )
    print(f'[EXTRACT] content={total_text_chars} chars, '
          f'max_tokens={extract_max_tokens}')

    messages = [{'role': 'user', 'content': content}]
    failures = []

    for i, model in enumerate(model_list):
        try:
            print(f'[EXTRACT] Trying {i+1}/{len(model_list)}: {model}')
            resp   = client.chat.completions.create(
                model=model, temperature=0,
                max_tokens=extract_max_tokens,
                messages=messages)
            choice = resp.choices[0] if resp.choices else None
            if not choice or not choice.message.content:
                raise ValueError('Empty response')
            parsed = _extract_json(choice.message.content)
            normed = _normalize(parsed)
            normed = _filter_dates(normed)
            normed = _ensure_study_days(normed, today_str)
            return normed
        except Exception as e:
            reason = f'[{model}] {type(e).__name__}: {e}'
            print(f'[ERROR] {reason}')
            failures.append(reason)

    raise RuntimeError('All extraction models failed:\n' + '\n'.join(failures))