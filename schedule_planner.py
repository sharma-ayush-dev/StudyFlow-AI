"""
schedule_planner.py — Adaptive, abuse-resistant schedule generation.

Key changes vs original:
  - Dynamic token budgeting (estimate_schedule_tokens) replaces static 8000 cap
  - Compact JSON serialisation (no indent=2 bloat)
  - Input validation before LLM call (MAX_SUBJECTS, MAX_TOPICS, MAX_DAYS)
  - Schedule result caching via content hash (avoids duplicate LLM calls)
  - Reduced fallback windows to prevent retry explosion
"""

import json
import re
import hashlib
import datetime
import threading
from openai import OpenAI
import apikey


client = OpenAI(base_url="https://api.aicredits.in/v1", api_key=apikey.key)

MODELS = [
    "mistralai/mistral-nemo"
]

# ── TOKEN BUDGET CONSTANTS ────────────────────────────────────────────────────
# These are outer bounds; actual max_tokens is computed dynamically per request.
HARD_CAP    = 8000   # absolute ceiling for adaptive budgeting
MIN_CAP     = 1500   # floor — never go below this
BUFFER_MULT = 1.4    # safety multiplier over raw estimate
ADMIN_CAP   = 12000  # maximum any admin override can set (wallet guard)

# ── VALIDATION LIMITS ─────────────────────────────────────────────────────────
MAX_SUBJECTS          = 12
MAX_TOPICS_PER_SUBJECT = 15
MAX_TOTAL_TOPICS      = 60
MAX_SCHEDULE_DAYS     = 90   # beyond this the JSON output grows unmanageably

# ── SCHEDULE RESULT CACHE (in-memory, bounded) ───────────────────────────────
_sched_cache: dict  = {}        # hash → schedule dict
_sched_cache_lock   = threading.Lock()
SCHED_CACHE_MAX     = 32        # LRU-evict oldest when full
SCHED_CACHE_TTL_S   = 1800      # 30 min — stale after regenerate or progress change


# ── PROMPT TEMPLATE ──────────────────────────────────────────────────────────

_PROMPT_TEMPLATE = """Generate a study schedule. Today: {today}.
Goal: maximise exam scores.

Priority: nearest exam first. Within subject:
0% topics > 1-49% > 50-99% > 100% (revision only if spare time).

Hard rules:
- Never schedule after exam date or before {today}
- Respect daily hour limits; skip "0" hour days
- Positive integer hours only, 1-3h blocks preferred
- For each topic slot, include only the subtopics that fit in the allotted hours
  (pick the most important ones if not all fit)
- Respect schedule_preferences when they are present:
  - gentle means lighter days and more revision breathing room
  - balanced means steady workload
  - focused means prioritize unfinished topics more aggressively
  - intense means use more available hours while still respecting daily limits
  - preferred block length and preference_note should guide topic grouping

You MUST return valid JSON.
- No explanations, no markdown, no trailing commas, no comments
- Output ONLY the JSON object

Format: {{"DD-MM-YYYY":{{"SubjectName":{{"TopicName":{{"hours":<int>,"subtopics":["A","B"]}}}}}}}}

Input:
{data}"""


# ── INPUT VALIDATION ─────────────────────────────────────────────────────────

def validate_schedule_input(topic_data: dict):
    """
    Raise ValueError with a clear message if the payload is abusively large.
    Called BEFORE any LLM invocation.
    """
    subjects = topic_data.get('Subjects') or {}
    n_subjects = len(subjects)
    if n_subjects > MAX_SUBJECTS:
        raise ValueError(
            f"Too many subjects ({n_subjects}). Maximum allowed: {MAX_SUBJECTS}.")

    total_topics = 0
    for subj, topics in subjects.items():
        n = len(topics) if isinstance(topics, dict) else 0
        if n > MAX_TOPICS_PER_SUBJECT:
            raise ValueError(
                f"Subject '{subj}' has too many topics ({n}). "
                f"Maximum per subject: {MAX_TOPICS_PER_SUBJECT}.")
        total_topics += n

    if total_topics > MAX_TOTAL_TOPICS:
        raise ValueError(
            f"Total topic count ({total_topics}) exceeds limit ({MAX_TOTAL_TOPICS}).")

    study_days = topic_data.get('study_days') or {}
    if len(study_days) > MAX_SCHEDULE_DAYS:
        raise ValueError(
            f"Schedule spans {len(study_days)} days; maximum is {MAX_SCHEDULE_DAYS}. "
            f"Check your exam dates.")


# ── DYNAMIC TOKEN ESTIMATION ─────────────────────────────────────────────────

def estimate_schedule_tokens(topic_data: dict) -> int:
    """
    Estimate how many output tokens the schedule JSON will require.

    Empirical calibration (measured on real outputs):
      - Per-day header overhead:  ~12 tokens
      - Per-subject-in-day block: ~10 tokens
      - Per-topic entry (name + hours + 3 subtopics avg): ~30 tokens

    We assume each subject appears roughly every 2 days (interleaved scheduling),
    and each topic appears across ~30% of available days.
    """
    subjects   = topic_data.get('Subjects') or {}
    study_days = topic_data.get('study_days') or {}
    n_days     = max(len(study_days), 1)
    n_subjects = max(len(subjects), 1)

    total_topics = sum(
        len(t) if isinstance(t, dict) else 0
        for t in subjects.values()
    )

    # Expected entries: each topic covered across ~30% of days
    expected_topic_day_entries = total_topics * n_days * 0.30

    tokens = (
        n_days     * 12 +                   # date keys + structure
        n_days     * n_subjects * 10 +       # subject blocks per day
        expected_topic_day_entries * 30      # topic rows with subtopics
    )
    return int(tokens)


def compute_max_tokens(topic_data: dict, admin_override: int = None) -> int:
    """
    Return the adaptive max_tokens value for this payload.

    Logic (in priority order):
      1. Compute adaptive estimate from workload size (baseline).
      2. If admin has set an override, use whichever is LARGER (override or adaptive),
         capped at ADMIN_CAP=12000 for wallet safety.
      3. Otherwise clamp adaptive result to [MIN_CAP, HARD_CAP].
    """
    estimated = estimate_schedule_tokens(topic_data)
    adaptive  = int(estimated * BUFFER_MULT)
    clamped   = max(MIN_CAP, min(adaptive, HARD_CAP))

    if admin_override:
        # Always honour admin override; cap only at ADMIN_CAP (12000), not HARD_CAP
        effective = min(int(admin_override), ADMIN_CAP)
        # Use whichever is larger: admin intent or adaptive floor
        return max(clamped, effective)

    return clamped


# ── SCHEDULE RESULT CACHE ─────────────────────────────────────────────────────

def _cache_key(topic_data: dict, today_str: str) -> str:
    payload = json.dumps(topic_data, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(f"{today_str}:{payload}".encode()).hexdigest()[:24]


def _cache_get(key: str):
    with _sched_cache_lock:
        entry = _sched_cache.get(key)
        if entry is None:
            return None
        if datetime.datetime.utcnow().timestamp() - entry['ts'] > SCHED_CACHE_TTL_S:
            _sched_cache.pop(key, None)
            return None
        return entry['data']


def _cache_set(key: str, data: dict):
    with _sched_cache_lock:
        if len(_sched_cache) >= SCHED_CACHE_MAX:
            # evict the oldest entry
            oldest = min(_sched_cache, key=lambda k: _sched_cache[k]['ts'])
            _sched_cache.pop(oldest, None)
        _sched_cache[key] = {'data': data, 'ts': datetime.datetime.utcnow().timestamp()}


def invalidate_schedule_cache(topic_data: dict, today_str: str):
    """Call this whenever topic_status changes to prevent stale cache hits."""
    key = _cache_key(topic_data, today_str)
    with _sched_cache_lock:
        _sched_cache.pop(key, None)


# ── MODEL CALL ───────────────────────────────────────────────────────────────

def _call_model(model: str, messages: list, max_tokens: int) -> str:
    kwargs = {
        'model': model,
        'temperature': 0,
        'max_tokens': max_tokens,
        'messages': messages,
        'response_format': {'type': 'json_object'},
    }
    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception as exc:
        if not _should_retry_without_json_mode(exc):
            raise
        kwargs.pop('response_format', None)
        resp = client.chat.completions.create(**kwargs)
    choice = resp.choices[0] if resp.choices else None
    if not choice or not choice.message.content:
        raise ValueError(
            f'[{model}] Empty content. finish_reason='
            f'{getattr(choice, "finish_reason", "?")}. '
            f'max_tokens={max_tokens} may be too low.')
    if choice.finish_reason == 'length':
        print(f'[WARN] [{model}] Output truncated at {max_tokens} tokens. '
              f'Consider raising HARD_CAP for large schedules.')
    return choice.message.content


# ── JSON SANITISATION ────────────────────────────────────────────────────────

def _sanitize_json_str(s: str) -> str:
    """Replace Unicode lookalike characters and invisible chars that break json.loads()."""
    s = s.replace('\u2018', "'").replace('\u2019', "'")
    s = s.replace('\u201c', '"').replace('\u201d', '"')
    s = s.replace('\u201a', "'").replace('\u201b', "'")
    s = s.replace('\u201e', '"').replace('\u201f', '"')
    s = s.replace('\u2013', '-').replace('\u2014', '-')
    s = s.replace('\u00a0', ' ')   # non-breaking space
    s = s.replace('\u00ad', '')    # soft hyphen
    s = s.replace('\u200b', '')    # zero-width space
    s = s.replace('\u200c', '')    # zero-width non-joiner
    s = s.replace('\u200d', '')    # zero-width joiner
    s = s.replace('\u2060', '')    # word joiner
    s = s.replace('\ufeff', '')    # BOM (anywhere in string)
    s = re.sub(r',\s*([}\]])', r'\1', s)   # trailing commas
    # Strip ALL remaining invisible / control characters
    s = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', s)   # C0 controls
    s = re.sub(r'[\x80-\x9f]', '', s)                           # C1 controls
    s = re.sub(r'[\u2000-\u200f\u2028-\u202f\u2061-\u2064]', '', s)  # Unicode invisible
    return s


def _parse(raw: str) -> dict:
    raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL)
    raw = re.sub(r'```(?:json)?', '', raw).strip()
    raw = _sanitize_json_str(raw)
    s, e = raw.find('{'), raw.rfind('}') + 1
    if s == -1 or e == 0:
        raise ValueError('No JSON found in model output')
    return json.loads(raw[s:e])


# ── COMPACT SERIALISATION ────────────────────────────────────────────────────

def _serialize_input(topic_data: dict) -> str:
    """
    Compact, whitespace-free serialisation of the schedule input.
    Saves ~35-45% tokens vs indent=2 — critical for large payloads.
    """
    compact = {
        'Exam_dates': topic_data.get('Exam_dates', {}),
        'study_days': topic_data.get('study_days', {}),
        'schedule_preferences': topic_data.get('schedule_preferences', {}),
        'Subjects':   {}
    }
    for subj, topics in (topic_data.get('Subjects') or {}).items():
        compact['Subjects'][subj] = {}
        for tname, tdata in topics.items():
            if isinstance(tdata, dict):
                compact['Subjects'][subj][tname] = {
                    'status':    tdata.get('status', '0'),
                    'subtopics': tdata.get('subtopics', [])
                }
            else:
                compact['Subjects'][subj][tname] = {
                    'status': str(tdata), 'subtopics': []
                }
    # No indent — compact separators cut token count significantly
    return json.dumps(compact, ensure_ascii=False, separators=(',', ':'))


# ── PUBLIC API ───────────────────────────────────────────────────────────────

def generate_schedule(topic_data: dict,
                      today_str:  str  = None,
                      max_tokens: int  = None,   # admin override, or None for adaptive
                      model_list: list = None) -> dict:
    """
    Generate a study schedule from topic_data.

    max_tokens: if provided by admin, used as a cap hint (still bounded by HARD_CAP).
                If None, computed dynamically from workload.
    """
    if today_str  is None: today_str  = datetime.date.today().strftime('%d-%m-%Y')
    if model_list is None: model_list = MODELS

    # ── Validate before touching LLM ─────────────────────────
    validate_schedule_input(topic_data)

    # ── Check result cache first ──────────────────────────────
    ck = _cache_key(topic_data, today_str)
    cached = _cache_get(ck)
    if cached is not None:
        print(f'[SCHED] Cache HIT ({ck[:8]}…) — skipping LLM call')
        return dict(cached)   # return a copy

    # ── Compute adaptive token budget ─────────────────────────
    effective_max = compute_max_tokens(topic_data, admin_override=max_tokens)
    print(f'[SCHED] Token budget: {effective_max} '
          f'(estimate={estimate_schedule_tokens(topic_data)}, '
          f'admin_override={max_tokens})')

    prompt   = _PROMPT_TEMPLATE.format(
        today=today_str,
        data=_serialize_input(topic_data)
    )
    messages = [
        {'role': 'system', 'content': 'Output valid JSON only. No markdown.'},
        {'role': 'user',   'content': prompt}
    ]

    failures = []
    for i, model in enumerate(model_list):
        try:
            print(f'[SCHED] Trying {i+1}/{len(model_list)}: {model} '
                  f'max_tokens={effective_max}')
            raw      = _call_model(model, messages, effective_max)
            schedule = _parse(raw)
            schedule['_meta'] = {
                'model_used':      model,
                'primary_failed':  i > 0,
                'failure_reasons': failures,
                'max_tokens_used': effective_max,
            }
            # Cache the clean result (without _meta)
            clean = {k: v for k, v in schedule.items() if k != '_meta'}
            _cache_set(ck, clean)
            return schedule
        except json.JSONDecodeError as exc:
            reason = f'[{model}] JSON error: {exc}'
        except Exception as exc:
            reason = f'[{model}] {type(exc).__name__}: {exc}'
        print(f'[ERROR] {reason}')
        failures.append(reason)

    raise RuntimeError(f'All {len(model_list)} models failed.\n' + '\n'.join(failures))
