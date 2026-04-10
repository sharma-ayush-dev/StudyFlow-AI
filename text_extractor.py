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

VISION_MODELS = [
    "qwen/qwen2-vl-72b-instruct",
    "google/gemini-2.0-flash-001",
    "meta-llama/llama-4-maverick",
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

    Handles both the new subtopic format and the old flat format gracefully.
    """
    if not isinstance(payload, dict):
        return {'Exam_dates': {}, 'Subjects': {}, 'study_days': {}}

    raw_s = _find(payload, 'Subjects', 'subjects') or {}
    raw_d = _find(payload, 'Exam_dates', 'exam_dates', 'ExamDates', 'examDates') or {}
    ns, nd = {}, {}

    # Helper: normalise a single topic value into the canonical shape
    def _norm_topic(val) -> dict:
        if isinstance(val, dict):
            subs = val.get('subtopics') or []
            if not isinstance(subs, list):
                subs = []
            subs = [str(s).strip() for s in subs if str(s).strip()]
            return {'status': 'none', 'subtopics': subs}
        # Old flat format ("none" / percentage string) — no subtopics
        return {'status': 'none', 'subtopics': []}

    if isinstance(raw_s, list):
        # Very old array-of-objects format
        for s in raw_s:
            if not isinstance(s, dict): continue
            name = str(s.get('subject') or s.get('name') or '').strip()
            if not name: continue
            topics_raw = s.get('topics') or {}
            if isinstance(topics_raw, list):
                # list of topic names with no subtopics
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
            # else: unexpected shape, skip
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
        parts.append({'type': 'image_url',
                      'image_url': {'url': f'data:{mime};base64,{b64}'}})
    elif ext == '.pdf':
        parts.append({'type': 'text', 'text': extract_pdf(path) or '(empty)'})
    elif ext == '.docx':
        parts.append({'type': 'text', 'text': extract_docx(path) or '(empty)'})
    else:
        raise Exception(f'Unsupported: {ext}')
    return parts


# ── PROMPT ───────────────────────────────────────────────────

# Updated prompt requesting subtopic arrays
_PROMPT = """Extract study planner data from the SYLLABUS and DATESHEET provided.

Return ONLY this JSON (no markdown, no explanation):
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


# ── MAIN ─────────────────────────────────────────────────────

def organize_with_llm(file_paths: list,
                      manual_text: str = None,
                      today_str:   str = None,
                      model_list:  list = None) -> dict:
    if today_str  is None: today_str  = datetime.date.today().strftime('%d-%m-%Y')
    if model_list is None: model_list = VISION_MODELS
    if not file_paths and not manual_text:
        raise ValueError('No input provided')

    content = []
    if file_paths:
        for i, p in enumerate(file_paths[:-1]):
            content.extend(_file_parts(p, f'SYLLABUS {i+1}'))
        content.extend(_file_parts(file_paths[-1], 'DATESHEET'))
    if manual_text:
        content.append({'type': 'text',
                        'text': f'\n---MANUAL SYLLABUS---\n{manual_text}\n'})
    content.append({'type': 'text', 'text': _PROMPT})

    messages   = [{'role': 'user', 'content': content}]
    failures   = []

    for i, model in enumerate(model_list):
        try:
            print(f'[EXTRACT] Trying {i+1}/{len(model_list)}: {model}')
            resp   = client.chat.completions.create(
                model=model, temperature=0, messages=messages)
            choice = resp.choices[0] if resp.choices else None
            if not choice or not choice.message.content:
                raise ValueError(f'Empty response')
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