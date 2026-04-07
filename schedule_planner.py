import json
import re
import datetime
from openai import OpenAI
import apikey


client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=apikey.key
)

# ─────────────────────────────────────────────
# MODEL FALLBACK LIST
# Tried in order. If a model returns None content or raises an exception,
# the next one is tried automatically.
# ─────────────────────────────────────────────
MODELS = [
    "qwen/qwen3-235b-a22b",             # primary — best quality
    "qwen/qwen2.5-72b-instruct",        # fallback 1 — fast, reliable
    "google/gemini-2.0-flash-001",      # fallback 2 — different provider
]

DEFAULT_MAX_TOKENS = 4000   # raised from 3000 — schedules for many subjects can be long


# ─────────────────────────────────────────────
# INTERNAL: CALL ONE MODEL
# Returns (raw_content: str, model_used: str) or raises on hard failure.
# ─────────────────────────────────────────────

def _call_model(model: str, messages: list, max_tokens: int) -> str:
    """
    Calls a single model and returns the raw text content.
    Raises ValueError if the response content is None or empty
    (e.g. hit max_tokens with finish_reason='length', or API returned nothing).
    Raises any OpenAI/network exception as-is so the caller can try the next model.
    """
    response = client.chat.completions.create(
        model=model,
        temperature=0.2,
        max_tokens=max_tokens,
        messages=messages
    )

    choice = response.choices[0] if response.choices else None

    if choice is None:
        raise ValueError(f"[{model}] API returned no choices at all")

    content       = choice.message.content
    finish_reason = choice.finish_reason

    # Detailed diagnostics for the admin log
    print(f"[LLM] model={model}  finish_reason={finish_reason}  "
          f"content_len={len(content) if content else 'None'}")

    if not content:
        raise ValueError(
            f"[{model}] Content is None/empty. "
            f"finish_reason={finish_reason}. "
            f"Likely cause: max_tokens ({max_tokens}) too low and response was cut off, "
            f"or the model returned a non-text response."
        )

    if finish_reason == 'length':
        # Content exists but was truncated — try to parse what we got,
        # but warn loudly so the admin knows to raise max_tokens.
        print(f"[WARN] [{model}] Response truncated (finish_reason=length). "
              f"max_tokens={max_tokens} may be too low. "
              f"Attempting to parse partial response.")

    return content


# ─────────────────────────────────────────────
# INTERNAL: STRIP LLM NOISE AND PARSE JSON
# ─────────────────────────────────────────────

def _parse_schedule_json(raw: str) -> dict:
    """Strip thinking blocks / fences and extract the JSON object."""
    raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL)
    raw = re.sub(r'```(?:json)?', '', raw).strip()
    start = raw.find('{')
    end   = raw.rfind('}') + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON object found in model output")
    return json.loads(raw[start:end])


# ─────────────────────────────────────────────
# PUBLIC: GENERATE SCHEDULE
# ─────────────────────────────────────────────

def generate_schedule(topic_data:  dict,
                      today_str:   str  = None,
                      max_tokens:  int  = None,
                      model_list:  list = None) -> dict:
    """
    topic_data  — payload from Status / Progress page:
    {
      "Exam_dates": { "Maths": "03-02-2026" },
      "Subjects":   { "Maths": { "Algebra": "0", "Calculus": "75" } },
      "study_days": { "DD-MM-YYYY": "hours_string" }
    }

    today_str   — DD-MM-YYYY (admin override or real date).
    max_tokens  — override the default (useful for large schedules). Falls back to
                  DEFAULT_MAX_TOKENS if not provided.
    model_list  — override the MODELS fallback list (admin-configurable).

    Returns:
    {
      "DD-MM-YYYY": { "SubjectName": { "TopicName": <integer hours> } }
    }

    Also returns metadata in a sidecar dict stored at the '_meta' key — the caller
    (app.py) strips this before saving to the DB but uses it for notifications:
    {
      "_meta": {
          "model_used":       "qwen/qwen2.5-72b-instruct",
          "primary_failed":   True,
          "failure_reasons":  ["[qwen/qwen3-235b-a22b] Content is None ..."],
          "finish_reason":    "stop"
      }
    }
    """
    if today_str is None:
        today_str = datetime.date.today().strftime('%d-%m-%Y')

    if max_tokens is None:
        max_tokens = DEFAULT_MAX_TOKENS

    if model_list is None:
        model_list = MODELS

    prompt = f"""You are an expert exam preparation planner. Your single objective is to maximise the student's overall exam score across all subjects.

TODAY'S DATE: {today_str}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INPUT FORMAT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You receive a JSON object with three keys:
  • "Exam_dates"  — subject → exam date (DD-MM-YYYY)
  • "Subjects"    — subject → {{ topic → completion_percentage (0–100 as a string) }}
  • "study_days"  — date → available hours (as a string; "0" means no study that day)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SCHEDULING RULES  (follow strictly)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1.  EXAM PROXIMITY FIRST — prioritise subjects with the nearest exam date.
2.  TOPIC PRIORITY (score-maximising):
    a. 0 %    — highest priority.
    b. 1–49 % — medium priority.
    c. 50–99% — lower priority.
    d. 100%   — revision ONLY if all incomplete topics are already covered
                AND spare hours remain. Never displace 0–99% topics.
3.  HARD CONSTRAINTS:
    • Never schedule a topic on or after its exam date.
    • Never schedule on a date before TODAY ({today_str}).
    • Never exceed the available hours for a day.
    • Skip days with "0" available hours.
    • All hour values must be positive integers (1, 2, 3…).
4.  Spread topics across days; prefer 1–3 hour blocks per topic per day.
5.  Subjects with no exam date get lowest priority.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT FORMAT — return ONLY this JSON, nothing else:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{{
  "DD-MM-YYYY": {{
    "SubjectName": {{
      "TopicName": <integer hours>
    }}
  }}
}}
Omit days with 0 hours, omit subjects/topics not assigned on a day.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INPUT DATA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{json.dumps(topic_data, ensure_ascii=False, indent=2)}
"""

    messages = [
        {
            'role':    'system',
            'content': 'You are a study schedule optimiser. Output valid JSON only — no prose, no markdown.'
        },
        {
            'role':    'user',
            'content': prompt
        }
    ]

    failure_reasons = []
    last_exception  = None

    for i, model in enumerate(model_list):
        try:
            print(f"[LLM] Trying model {i+1}/{len(model_list)}: {model}")
            raw      = _call_model(model, messages, max_tokens)
            schedule = _parse_schedule_json(raw)

            # Attach metadata for app.py to consume then strip
            schedule['_meta'] = {
                'model_used':      model,
                'primary_failed':  i > 0,
                'failure_reasons': failure_reasons,
            }
            return schedule

        except json.JSONDecodeError as e:
            reason = f"[{model}] JSON parse error: {e}"
            print(f"[ERROR] {reason}")
            failure_reasons.append(reason)
            last_exception = e

        except Exception as e:
            reason = f"[{model}] {type(e).__name__}: {e}"
            print(f"[ERROR] {reason}")
            failure_reasons.append(reason)
            last_exception = e

    # All models failed
    raise RuntimeError(
        f"All {len(model_list)} models failed to generate a schedule.\n"
        + "\n".join(failure_reasons)
    ) from last_exception