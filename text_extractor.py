import os
import re
import json
import base64
import datetime
from pypdf import PdfReader
from docx import Document
from openai import OpenAI
import apikey


client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=apikey.key)

# ── FALLBACK MODEL LIST ──────────────────────────────────────
# Tried in order — same pattern as schedule_planner.py.
# Export so app.py can display and let admin override them.
VISION_MODELS = [
    "qwen/qwen2-vl-72b-instruct",        # primary
    "google/gemini-2.0-flash-001",        # fallback 1
    "meta-llama/llama-4-maverick",        # fallback 2
]


# ── DATE UTILS ───────────────────────────────────────────────

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


# ── NORMALISATION ────────────────────────────────────────────

def _find(d: dict, *keys):
    for k in keys:
        if k in d: return d[k]
    lo = {x.lower(): d[x] for x in d}
    for k in keys:
        if k.lower() in lo: return lo[k.lower()]
    return None


def _normalize(payload) -> dict:
    if not isinstance(payload, dict):
        return {'Exam_dates': {}, 'Subjects': {}, 'study_days': {}}

    raw_s = _find(payload, 'Subjects', 'subjects') or {}
    raw_d = _find(payload, 'Exam_dates', 'exam_dates', 'ExamDates', 'examDates') or {}
    ns, nd = {}, {}

    if isinstance(raw_s, list):
        for s in raw_s:
            if not isinstance(s, dict): continue
            name = str(s.get('subject') or s.get('name') or '').strip()
            if not name: continue
            topics = s.get('topics') or []
            if isinstance(topics, list):
                topics = {str(t).strip(): 'none' for t in topics if isinstance(t, str) and t.strip()}
            elif isinstance(topics, dict):
                topics = {str(k).strip(): 'none' for k in topics if str(k).strip()}
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
        for subj, topics in raw_s.items():
            subj = str(subj).strip()
            if not subj: continue
            if isinstance(topics, list):
                topics = {str(t).strip(): 'none' for t in topics if t}
            elif isinstance(topics, str):
                topics = {t.strip(): 'none' for t in topics.split('\n') if t.strip()}
            elif not isinstance(topics, dict):
                topics = {}
            nt = {str(k).strip(): 'none' for k in topics if str(k).strip()}
            if nt: ns[subj] = nt
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
    if not valid:
        payload['study_days'] = {}
    else:
        payload['study_days'] = _date_range(today_str, _format_date(max(valid)))
    return payload


def _extract_json(text: str) -> dict:
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'```(?:json)?', '', text).strip()
    s, e = text.find('{'), text.rfind('}') + 1
    if s == -1 or e == 0: raise ValueError('No JSON found')
    return json.loads(text[s:e])


# ── FILE READERS ─────────────────────────────────────────────

def extract_pdf(path: str) -> str:
    return '\n'.join(p.extract_text() or '' for p in PdfReader(path).pages)

def extract_docx(path: str) -> str:
    return '\n'.join(p.text for p in Document(path).paragraphs)

def extract(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == '.pdf':  return extract_pdf(path)
    if ext == '.docx': return extract_docx(path)
    raise Exception(f'extract() does not support {ext}')


def _file_parts(path: str, label: str) -> list:
    ext   = os.path.splitext(path)[1].lower()
    parts = [{'type': 'text', 'text': f'\n---{label}---\n'}]
    if ext in ('.png', '.jpg', '.jpeg', '.webp'):
        with open(path, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode()
        mime = 'image/png' if ext == '.png' else 'image/jpeg'
        parts.append({'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{b64}'}})
    elif ext == '.pdf':
        parts.append({'type': 'text', 'text': extract_pdf(path) or '(empty)'})
    elif ext == '.docx':
        parts.append({'type': 'text', 'text': extract_docx(path) or '(empty)'})
    else:
        raise Exception(f'Unsupported: {ext}')
    return parts


# ── MAIN ─────────────────────────────────────────────────────

# Compact prompt — reduces token usage significantly
_PROMPT = """Extract structured study planner data from the provided SYLLABUS and DATESHEET sources.

Return ONLY this JSON (no markdown, no explanation):
{"Exam_dates":{"SubjectName":"DD-MM-YYYY"},"Subjects":{"SubjectName":{"Topic 1":"none","Topic 2":"none"}}}

Rules:
- Only include subjects present in SYLLABUS sources
- 4-8 topics per subject, prefer chapter/unit titles
- Dates in DD-MM-YYYY format; omit if not found for a subject"""


def organize_with_llm(file_paths: list,
                      manual_text: str = None,
                      today_str:   str = None,
                      model_list:  list = None) -> dict:
    if today_str is None:
        today_str = datetime.date.today().strftime('%d-%m-%Y')
    if model_list is None:
        model_list = VISION_MODELS
    if not file_paths and not manual_text:
        raise ValueError('No input provided')

    content = []
    if file_paths:
        for i, p in enumerate(file_paths[:-1]):
            content.extend(_file_parts(p, f'SYLLABUS {i+1}'))
        content.extend(_file_parts(file_paths[-1], 'DATESHEET'))
    if manual_text:
        content.append({'type': 'text', 'text': f'\n---MANUAL SYLLABUS---\n{manual_text}\n'})
    content.append({'type': 'text', 'text': _PROMPT})

    messages = [{'role': 'user', 'content': content}]
    failure_reasons = []

    for i, model in enumerate(model_list):
        try:
            print(f'[EXTRACT] Trying model {i+1}/{len(model_list)}: {model}')
            resp    = client.chat.completions.create(model=model, temperature=0, messages=messages)
            choice  = resp.choices[0] if resp.choices else None
            if not choice or not choice.message.content:
                raise ValueError(f'[{model}] Empty response (finish_reason={getattr(choice, "finish_reason", "?")})')
            raw     = choice.message.content
            parsed  = _extract_json(raw)
            normed  = _normalize(parsed)
            normed  = _filter_dates(normed)
            normed  = _ensure_study_days(normed, today_str)
            return normed
        except Exception as e:
            reason = f'[{model}] {type(e).__name__}: {e}'
            print(f'[ERROR] {reason}')
            failure_reasons.append(reason)

    print('LLM FAILURES:\n' + '\n'.join(failure_reasons))
    raise RuntimeError(f'All extraction models failed:\n' + '\n'.join(failure_reasons))