import json
import re
import datetime
from openai import OpenAI
import apikey


client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=apikey.key)

MODELS = [
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "nvidia/nemotron-nano-12b-v2-vl:free",
    "openai/gpt-oss-20b:free"
]

DEFAULT_MAX_TOKENS = 8000


# ── COMPACT PROMPT ───────────────────────────────────────────
# Input Subjects schema:
#   { "SubjectName": { "TopicName": { "status": "0-100", "subtopics": [...] } } }
#
# Output schedule schema:
#   { "DD-MM-YYYY": { "SubjectName": { "TopicName": { "hours": N, "subtopics": [...] } } } }

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

You MUST return valid JSON.
- No explanations
- No markdown
- No trailing commas
- No comments
- Ensure it parses with json.loads()

If you fail, the system will reject your output:
{{"DD-MM-YYYY":{{"SubjectName":{{"TopicName":{{"hours":<int>,"subtopics":["Subtopic A","Subtopic B"]}}}}}}}}

Input:
{data}"""


def _call_model(model: str, messages: list, max_tokens: int) -> str:
    resp   = client.chat.completions.create(
        model=model, temperature=0.2, max_tokens=max_tokens, messages=messages)
    choice = resp.choices[0] if resp.choices else None
    if not choice or not choice.message.content:
        raise ValueError(
            f'[{model}] Empty content. finish_reason='
            f'{getattr(choice,"finish_reason","?")}. '
            f'max_tokens={max_tokens} may be too low.')
    if choice.finish_reason == 'length':
        print(f'[WARN] [{model}] Truncated. Raise max_tokens above {max_tokens}.')
    return choice.message.content


def _parse(raw: str) -> dict:
    raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL)
    raw = re.sub(r'```(?:json)?', '', raw).strip()
    s, e = raw.find('{'), raw.rfind('}') + 1
    if s == -1 or e == 0: raise ValueError('No JSON found')
    return json.loads(raw[s:e])


def _serialize_input(topic_data: dict) -> str:
    """
    Prepare compact input for the prompt.
    topic_data.Subjects has new schema:
      { "SubjectName": { "TopicName": { "status": "N", "subtopics": [...] } } }
    We send a compact version to save tokens.
    """
    compact = {
        'Exam_dates': topic_data.get('Exam_dates', {}),
        'study_days': topic_data.get('study_days', {}),
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
                # backward compat: flat string status
                compact['Subjects'][subj][tname] = {
                    'status': str(tdata), 'subtopics': []
                }
    return json.dumps(compact, ensure_ascii=False, indent=2)


def generate_schedule(topic_data: dict,
                      today_str:  str  = None,
                      max_tokens: int  = None,
                      model_list: list = None) -> dict:
    if today_str  is None: today_str  = datetime.date.today().strftime('%d-%m-%Y')
    if max_tokens is None: max_tokens = DEFAULT_MAX_TOKENS
    if model_list is None: model_list = MODELS

    prompt   = _PROMPT_TEMPLATE.format(
        today=today_str,
        data=_serialize_input(topic_data)
    )
    messages = [
        {'role': 'system', 'content': 'Output valid JSON only.'},
        {'role': 'user',   'content': prompt}
    ]

    failures = []
    for i, model in enumerate(model_list):
        try:
            print(f'[SCHED] Trying {i+1}/{len(model_list)}: {model}')
            raw      = _call_model(model, messages, max_tokens)
            schedule = _parse(raw)
            schedule['_meta'] = {
                'model_used':      model,
                'primary_failed':  i > 0,
                'failure_reasons': failures
            }
            return schedule
        except json.JSONDecodeError as exc:
            reason = f'[{model}] JSON error: {exc}'
        except Exception as exc:
            reason = f'[{model}] {type(exc).__name__}: {exc}'
        print(f'[ERROR] {reason}')
        failures.append(reason)

    raise RuntimeError(f'All {len(model_list)} models failed.\n' + '\n'.join(failures))