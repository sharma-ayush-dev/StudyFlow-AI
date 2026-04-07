import json
import re
import datetime
from openai import OpenAI
import apikey


client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=apikey.key)

MODELS = [
    "qwen/qwen3-235b-a22b",
    "qwen/qwen2.5-72b-instruct",
    "google/gemini-2.0-flash-001",
]

DEFAULT_MAX_TOKENS = 4000

# Compact prompt — reduced from ~500 tokens to ~150
_PROMPT_TEMPLATE = """Generate a study schedule. Today: {today}.
Goal: maximise exam scores.

Priority order:
1. Nearest exam date first
2. Within subject: 0% topics > 1-49% > 50-99% > 100% (revision only if spare time)
3. Never schedule after exam date or before {today}
4. Respect daily hour limits (skip days with "0" hours)
5. Positive integer hours only, 1-3h blocks preferred

Return ONLY JSON:
{{"DD-MM-YYYY":{{"SubjectName":{{"TopicName":<hours>}}}}}}
Omit days with 0 hours, omit unassigned subjects/topics.

Input:
{data}"""


def _call_model(model: str, messages: list, max_tokens: int) -> str:
    resp   = client.chat.completions.create(
        model=model, temperature=0.2, max_tokens=max_tokens, messages=messages)
    choice = resp.choices[0] if resp.choices else None
    if not choice or not choice.message.content:
        raise ValueError(
            f'[{model}] Empty content. finish_reason={getattr(choice,"finish_reason","?")}. '
            f'max_tokens={max_tokens} may be too low.')
    if choice.finish_reason == 'length':
        print(f'[WARN] [{model}] Truncated (finish_reason=length). '
              f'Consider raising max_tokens above {max_tokens}.')
    return choice.message.content


def _parse(raw: str) -> dict:
    raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL)
    raw = re.sub(r'```(?:json)?', '', raw).strip()
    s, e = raw.find('{'), raw.rfind('}') + 1
    if s == -1 or e == 0: raise ValueError('No JSON found in output')
    return json.loads(raw[s:e])


def generate_schedule(topic_data: dict,
                      today_str:  str  = None,
                      max_tokens: int  = None,
                      model_list: list = None) -> dict:
    """
    Returns the schedule dict with a '_meta' key that app.py strips before
    saving to DB.  '_meta' carries fallback info for user notification.
    """
    if today_str  is None: today_str  = datetime.date.today().strftime('%d-%m-%Y')
    if max_tokens is None: max_tokens = DEFAULT_MAX_TOKENS
    if model_list is None: model_list = MODELS

    prompt   = _PROMPT_TEMPLATE.format(
        today=today_str,
        data=json.dumps(topic_data, ensure_ascii=False, indent=2)
    )
    messages = [
        {'role': 'system', 'content': 'Output valid JSON only.'},
        {'role': 'user',   'content': prompt}
    ]

    failures = []
    for i, model in enumerate(model_list):
        try:
            print(f'[SCHED] Trying model {i+1}/{len(model_list)}: {model}')
            raw      = _call_model(model, messages, max_tokens)
            schedule = _parse(raw)
            schedule['_meta'] = {
                'model_used':      model,
                'primary_failed':  i > 0,
                'failure_reasons': failures
            }
            return schedule
        except json.JSONDecodeError as exc:
            reason = f'[{model}] JSON parse error: {exc}'
        except Exception as exc:
            reason = f'[{model}] {type(exc).__name__}: {exc}'
        print(f'[ERROR] {reason}')
        failures.append(reason)

    raise RuntimeError(
        f'All {len(model_list)} models failed.\n' + '\n'.join(failures))