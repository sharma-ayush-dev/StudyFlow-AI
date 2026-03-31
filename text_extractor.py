import os
import base64
import json
import re
from pypdf import PdfReader
from docx import Document
from openai import OpenAI
import apikey
import datetime


today_date = datetime.date.today().strftime("%d-%m-%Y")

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=apikey.key
)

# Vision-capable model — reads images and text in a single call.
# Swap this for any vision model available on your OpenRouter plan,
# e.g. "google/gemini-flash-1.5", "meta-llama/llama-4-maverick"
VISION_MODEL = "nvidia/nemotron-nano-12b-v2-vl:free"


# -----------------------------------------------
# DATE UTILITIES
# -----------------------------------------------

def _parse_date(date_str: str) -> datetime.date:
    return datetime.datetime.strptime(date_str.strip(), "%d-%m-%Y").date()


def _format_date(date_obj: datetime.date) -> str:
    return date_obj.strftime("%d-%m-%Y")


def _date_range_inclusive(start_str: str, end_str: str) -> dict:
    """Return {DD-MM-YYYY: 'none'} for every day from start to end inclusive."""
    start = _parse_date(start_str)
    end   = _parse_date(end_str)
    out, cur = {}, start
    while cur <= end:
        out[_format_date(cur)] = "none"
        cur += datetime.timedelta(days=1)
    return out


# -----------------------------------------------
# PAYLOAD NORMALISATION
# -----------------------------------------------

def _find_key(d: dict, *candidates):
    """
    Case-insensitive dict lookup.
    Tries each candidate name exactly first, then lowercased.
    Returns the value for the first match, or None.
    """
    for k in candidates:
        if k in d:
            return d[k]
    lower_map = {dk.lower(): d[dk] for dk in d}
    for k in candidates:
        if k.lower() in lower_map:
            return lower_map[k.lower()]
    return None


def _normalize_payload(payload) -> dict:
    """
    Accepts ANY shape the LLM might return and produces the canonical form:
    {
      "Exam_dates": { "SubjectName": "DD-MM-YYYY" },
      "Subjects":   { "SubjectName": { "TopicName": "none" } },
      "study_days": {}   <- filled later by _ensure_study_days
    }

    Handles all of:
    - Capital/lowercase key variants  (Subjects / subjects / SUBJECTS)
    - New dict format  { "Subjects": { "Maths": { "Topic": "none" } } }
    - Old array format { "subjects": [{ "subject": "Maths", "topics": [...] }] }
    - Topics as list, dict, or newline-separated string
    """
    if not isinstance(payload, dict):
        return {"Exam_dates": {}, "Subjects": {}, "study_days": {}}

    raw_subjects = _find_key(payload, "Subjects", "subjects") or {}
    raw_dates    = _find_key(payload,
                             "Exam_dates", "exam_dates", "ExamDates",
                             "Exam dates", "examDates") or {}

    norm_subjects: dict = {}
    norm_dates:    dict = {}

    # ── Old array format: subjects is a list of objects ──────────────
    if isinstance(raw_subjects, list):
        for s in raw_subjects:
            if not isinstance(s, dict):
                continue
            name = str(s.get("subject") or s.get("name") or "").strip()
            if not name:
                continue

            topics = s.get("topics") or []
            if isinstance(topics, list):
                topics_dict = {str(t).strip(): "none"
                               for t in topics if isinstance(t, str) and t.strip()}
            elif isinstance(topics, dict):
                topics_dict = {str(k).strip(): "none" for k in topics if str(k).strip()}
            else:
                topics_dict = {}

            if topics_dict:
                norm_subjects[name] = topics_dict

            # exam_date may be inline in the array item
            ed = s.get("exam_date") or s.get("examDate")
            if ed and str(ed).strip() not in ("null", "None", ""):
                norm_dates[name] = str(ed).strip()

        # Also merge any top-level Exam_dates dict
        if isinstance(raw_dates, dict):
            for k, v in raw_dates.items():
                if v and str(v).strip() not in ("null", "None", ""):
                    norm_dates[str(k).strip()] = str(v).strip()

    # ── New dict format: subjects is a dict of dicts ─────────────────
    elif isinstance(raw_subjects, dict):
        for subj, topics in raw_subjects.items():
            subj = str(subj).strip()
            if not subj:
                continue

            if isinstance(topics, list):
                topics = {str(t).strip(): "none" for t in topics if t}
            elif isinstance(topics, str):
                topics = {t.strip(): "none" for t in topics.split("\n") if t.strip()}
            elif not isinstance(topics, dict):
                topics = {}

            norm_topics = {str(k).strip(): "none" for k in topics if str(k).strip()}
            if norm_topics:
                norm_subjects[subj] = norm_topics

        if isinstance(raw_dates, dict):
            for subj, date in raw_dates.items():
                if date and str(date).strip() not in ("null", "None", ""):
                    norm_dates[str(subj).strip()] = str(date).strip()

    return {
        "Exam_dates": norm_dates,
        "Subjects":   norm_subjects,
        "study_days": {}
    }


def _filter_dates_to_known_subjects(payload: dict) -> dict:
    """Remove exam dates whose subject has no syllabus topics."""
    subjects   = payload.get("Subjects")   or {}
    exam_dates = payload.get("Exam_dates") or {}
    payload["Exam_dates"] = {s: d for s, d in exam_dates.items() if subjects.get(s)}
    return payload


def _ensure_study_days(payload: dict) -> dict:
    """Build study_days as {DD-MM-YYYY: 'none'} from today → last exam date."""
    exam_dates  = payload.get("Exam_dates") or {}
    valid_dates = []
    for d in exam_dates.values():
        if isinstance(d, str) and d:
            try:
                valid_dates.append(_parse_date(d))
            except ValueError:
                pass

    if not valid_dates:
        payload["study_days"] = {}
        return payload

    last_exam = max(valid_dates)
    payload["study_days"] = _date_range_inclusive(today_date, _format_date(last_exam))
    return payload


def _extract_json_from_llm(text: str) -> dict:
    """
    Robustly pull a JSON object out of raw LLM output.
    Strips:
    - <think>…</think> blocks (Qwen3 / thinking models)
    - markdown fences (```json … ```)
    - surrounding prose
    """
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'```(?:json)?', '', text)
    text = text.strip()

    start = text.find("{")
    end   = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON object found in LLM output")

    return json.loads(text[start:end])


# -----------------------------------------------
# FILE READERS  (used for text-based formats)
# -----------------------------------------------

def extract_pdf(path: str) -> str:
    reader = PdfReader(path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def extract_docx(path: str) -> str:
    doc = Document(path)
    return "\n".join(p.text for p in doc.paragraphs)


def extract(path: str) -> str:
    """Utility for direct single-file text extraction (PDF / DOCX only)."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return extract_pdf(path)
    elif ext == ".docx":
        return extract_docx(path)
    else:
        raise Exception(
            f"extract() does not support image files ({ext}). "
            "Pass the file path to organize_with_llm() instead."
        )


# -----------------------------------------------
# FILE → LLM CONTENT PARTS
# -----------------------------------------------

def _file_to_content_parts(path: str, label: str) -> list:
    """
    Convert a file into OpenAI message content parts.

    WHY THIS EXISTS:
    The datesheet is often a photograph of a printed timetable.
    Sending it as raw base64 pixels lets the vision model read the
    table directly — far more reliable than any OCR → text pipeline.

    Images  (.png / .jpg)  → base64 image_url  (vision model reads visually)
    PDFs    (.pdf)         → text via pypdf
    Word    (.docx)        → text via python-docx
    """
    ext   = os.path.splitext(path)[1].lower()
    parts = [{"type": "text",
              "text": f"\n\n--- {label} ({os.path.basename(path)}) ---\n"}]

    if ext in ('.png', '.jpg', '.jpeg', '.webp'):
        with open(path, 'rb') as f:
            b64  = base64.b64encode(f.read()).decode()
        mime = 'image/png' if ext == '.png' else 'image/jpeg'
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"}
        })

    elif ext == '.pdf':
        parts.append({"type": "text", "text": extract_pdf(path) or "(empty PDF)"})

    elif ext == '.docx':
        parts.append({"type": "text", "text": extract_docx(path) or "(empty DOCX)"})

    else:
        raise Exception(f"Unsupported file type: {ext}")

    return parts


# -----------------------------------------------
# MAIN: ORGANIZE WITH LLM
# -----------------------------------------------

def organize_with_llm(file_paths: list) -> dict:
    """
    Accepts a list of saved file paths.
    Upload-page.js order: syllabus file(s) first, datesheet LAST.

    All files are sent in ONE multimodal LLM call:
    - Syllabus PDFs / DOCX  → extracted text
    - Datesheet image       → base64  (vision model reads the table directly)

    Returns the canonical payload:
    {
      "Exam_dates":  { "SubjectName": "DD-MM-YYYY" },
      "Subjects":    { "SubjectName": { "TopicName": "none" } },
      "study_days":  { "DD-MM-YYYY": "none" }
    }
    """
    if not file_paths:
        raise ValueError("No file paths provided to organize_with_llm()")

    syllabus_paths = file_paths[:-1]
    datesheet_path = file_paths[-1]

    # Build the multimodal message
    content: list = []

    for i, path in enumerate(syllabus_paths):
        content.extend(_file_to_content_parts(path, f"SYLLABUS {i + 1}"))

    content.extend(_file_to_content_parts(datesheet_path, "DATESHEET"))

    content.append({"type": "text", "text": """

You are preparing structured data for a study planner.

You have been given:
- One or more SYLLABUS files — these contain the subject names and their topics.
- One DATESHEET file — this contains exam dates (may be a photographed printed table).

YOUR TASKS:
1. From the SYLLABUS file(s), extract each subject name and its topics.
2. From the DATESHEET file, read the exam dates for each subject.
3. Match each exam date to the correct syllabus subject (allow for minor name differences).
4. CRITICAL: Only include subjects that appear in the SYLLABUS file(s).
   If a subject appears only in the datesheet with no matching syllabus, exclude it entirely.

Rules for topics:
- Extract 4–8 meaningful topics per subject.
- Prefer unit or chapter titles when available.
- All topic values must be the string "none".

Rules for dates:
- Output all dates in DD-MM-YYYY format (e.g. 02-04-2026).
- If no exam date can be found for a syllabus subject, omit it from Exam_dates.

Return ONLY valid JSON — no explanation, no markdown fences — in exactly this format:
{
  "Exam_dates": {
    "SubjectName": "DD-MM-YYYY"
  },
  "Subjects": {
    "SubjectName": {
      "Topic 1": "none",
      "Topic 2": "none",
      "Topic 3": "none"
    }
  }
}
"""})

    response = client.chat.completions.create(
        model=VISION_MODEL,
        temperature=0,
        messages=[{"role": "user", "content": content}]
    )

    raw = response.choices[0].message.content

    try:
        parsed     = _extract_json_from_llm(raw)
        normalized = _normalize_payload(parsed)
        normalized = _filter_dates_to_known_subjects(normalized)
        normalized = _ensure_study_days(normalized)
        return normalized

    except Exception as e:
        print("=" * 60)
        print("LLM RAW OUTPUT (parsing failed):")
        print(raw)
        print("=" * 60)
        raise Exception(f"LLM JSON parsing failed: {e}")