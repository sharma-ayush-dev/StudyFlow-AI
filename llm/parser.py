import re
import json

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
    s = re.sub(r'[\u2000-\u200f\u2028-\u202f\u2061-\u2064]', '', s)
    return s

def _extract_json(text: str) -> dict:
    """
    Extracts and parses JSON object from LLM response content.
    """
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
