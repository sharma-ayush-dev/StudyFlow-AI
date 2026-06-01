"""
text_extractor.py — Document parsing + LLM extraction pipeline.
Decoupled version routing to extractors/ and llm/ packages.
"""

import os
import re
import logging
import datetime
from extractors.router import route_file
from llm.config import MODEL_CANDIDATES as VISION_MODELS
from llm.understanding import understand_document

logger = logging.getLogger(__name__)

# ── EXTRACTION LIMITS ─────────────────────────────────────────────────────────
MAX_TEXT_CHARS_PER_FILE = 12_000   # ~3000 tokens of text context per file
MAX_TOTAL_TEXT_CHARS    = 20_000   # aggregate cap across all files + manual text

# Token budget constants for LLM output
EXTRACT_MIN_TOKENS  = 2000
EXTRACT_MAX_TOKENS  = 4000
EXTRACT_BUFFER_MULT = 1.4

# ── BACKWARDS COMPATIBILITY WRAPPERS ─────────────────────────────────────────

def extract_pdf(path: str) -> str:
    """Wrapper returning raw text for backwards compatibility."""
    from extractors.pdf_extractor import extract_pdf as _new_extract_pdf
    return _new_extract_pdf(path)["text"]

def extract_docx(path: str) -> str:
    """Wrapper returning raw text for backwards compatibility."""
    from extractors.docx_extractor import extract_docx as _new_extract_docx
    return _new_extract_docx(path)["text"]

def extract(path: str) -> str:
    """Wrapper returning raw text for backwards compatibility."""
    return route_file(path)["text"]

# ── TEXT CLEANING LAYER ──────────────────────────────────────────────────────

def _trim_text(text: str, max_chars: int) -> str:
    """Truncate text to max_chars, appending a note if trimmed."""
    if len(text) <= max_chars:
        return text
    # Try to break at a paragraph boundary near the limit
    cut = text[:max_chars].rfind('\n')
    if cut < max_chars * 0.8:
        cut = max_chars
    return text[:cut] + '\n[...document truncated for processing...]'

def clean_extracted_text(text: str, max_chars: int) -> str:
    """
    Cleans raw extracted text:
    - Removes null and invalid control characters
    - Normalizes line breaks (collapses 3+ newlines to 2)
    - Removes excessive horizontal whitespace
    - Applies truncation limits
    """
    if not text:
        return ""
    
    # Strip nulls and controls
    text = text.replace('\x00', '')
    text = re.sub(r'[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    
    # Normalize line breaks to standard Unix format
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    # Normalize excessive spaces (keeping newlines intact)
    text = re.sub(r'[ \t]+', ' ', text)
    
    return _trim_text(text.strip(), max_chars)

# ── ADAPTIVE TOKEN BUDGET ────────────────────────────────────────────────────

def _estimate_extraction_tokens(content_char_count: int) -> int:
    """Estimate output tokens for extraction based on input content size."""
    est_subjects = max(1, content_char_count // 2000)
    est_output   = est_subjects * 300
    return int(est_output * EXTRACT_BUFFER_MULT)

# ── MAIN ENTRY POINT ─────────────────────────────────────────────────────────

def organize_with_llm(file_paths: list,
                      manual_text: str = None,
                      today_str:   str = None,
                      model_list:  list = None,
                      user_id:     int  = None) -> dict:
    """
    Processes files and manual text through document extractors, cleans the combined
    text, and utilizes the LLM understanding layer to extract structured planner JSON.
    """
    if today_str  is None: today_str  = datetime.date.today().strftime('%d-%m-%Y')
    if model_list is None: model_list = VISION_MODELS
    if not file_paths and not manual_text:
        raise ValueError('No input provided')

    total_text_chars = 0
    text_parts = []

    if file_paths:
        # The last file is logically assumed to be the datesheet, others are syllabi.
        for i, p in enumerate(file_paths[:-1]):
            result = route_file(p)
            raw = result.get("text", "") or ""
            cleaned = clean_extracted_text(raw, MAX_TEXT_CHARS_PER_FILE)
            
            label_header = f'\n---SYLLABUS {i+1}---\n'
            text_parts.append(label_header + cleaned)
            total_text_chars += len(label_header) + len(cleaned)

        # Process the final file as DATESHEET
        result = route_file(file_paths[-1])
        raw = result.get("text", "") or ""
        cleaned = clean_extracted_text(raw, MAX_TEXT_CHARS_PER_FILE)
        
        label_header = '\n---DATESHEET---\n'
        text_parts.append(label_header + cleaned)
        total_text_chars += len(label_header) + len(cleaned)

    if manual_text:
        cleaned = clean_extracted_text(manual_text, MAX_TEXT_CHARS_PER_FILE)
        label_header = '\n---MANUAL SYLLABUS---\n'
        text_parts.append(label_header + cleaned)
        total_text_chars += len(label_header) + len(cleaned)

    # Combine all parts into a single text payload
    combined_text = "\n".join(text_parts)

    # Global text-size guard (defense-in-depth)
    if total_text_chars > MAX_TOTAL_TEXT_CHARS:
        print(f'[EXTRACT] Total text {total_text_chars} chars — already trimmed per-file, '
              f'enforcing global limit truncation.')
        combined_text = clean_extracted_text(combined_text, MAX_TOTAL_TEXT_CHARS)
        total_text_chars = len(combined_text)

    # Adaptive token budget for extraction output
    extract_max_tokens = max(
        EXTRACT_MIN_TOKENS,
        min(_estimate_extraction_tokens(total_text_chars), EXTRACT_MAX_TOKENS)
    )
    print(f'[EXTRACT] content={total_text_chars} chars, max_tokens={extract_max_tokens}')

    # Delegate to LLM Understanding Layer
    return understand_document(
        clean_text=combined_text,
        today_str=today_str,
        model_list=model_list,
        max_output_tokens=extract_max_tokens,
        user_id=user_id
    )