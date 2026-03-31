import json
from openai import OpenAI
import apikey


client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=apikey.key
)

MODEL = "qwen/qwen3-vl-235b-a22b-thinking"


def generate_schedule(topic_data: dict) -> dict:
    """
    Input shape:
    {
      "Exam_dates": { "Maths": "03-02-2026", "Chem": "06-02-2026" },
      "Subjects": {
        "Maths": { "Algebra": "0", "Calculus": "50", "Trigonometry": "100" },
        "Chem":  { "Organic": "20", "Inorganic": "60", "Physical": "100" }
      },
      "study_days": { "01-02-2026": "5", "02-02-2026": "0", ... }
    }
    Topic values are completion percentages as strings ("0"–"100").
    study_days values are available hours as strings ("0" = skip that day).

    Returns:
    {
      "DD-MM-YYYY": {
        "SubjectName": { "TopicName": <integer hours> }
      }
    }
    Only days with allocated study time are included.
    Only subjects/topics that have hours assigned on a given day are included.
    """

    prompt = f"""You are an expert exam preparation planner. Your single objective is to maximise the student's overall exam score across all subjects.

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
1.  EXAM PROXIMITY FIRST
    Always prioritise subjects whose exam date is nearest.
    As a subject's exam date approaches, allocate it a larger share of available hours.

2.  TOPIC PRIORITY WITHIN A SUBJECT (score-maximising order)
    a. 0 %       — highest priority; these topics can most improve the score.
    b. 1–49 %    — medium priority; partially learnt, worthwhile to complete.
    c. 50–99 %   — lower priority; already partially covered.
    d. 100 %     — revision ONLY. Schedule revision for a 100 % topic only if:
                     • every incomplete/partial topic of that subject is already
                       covered in the remaining days, AND
                     • spare hours still exist on that day.
                   Never sacrifice time for 0–99 % topics to fit in 100 % revision.

3.  HARD CONSTRAINTS
    • Never schedule any topic on or after its subject's exam date.
    • Never exceed the available hours for a day (sum of all assigned hours ≤ day's hours).
    • Skip any day whose available hours = 0.
    • All assigned hour values must be positive integers (1, 2, 3 …). No decimals, no zeros.

4.  DISTRIBUTION
    • Spread topics across multiple days; do not front-load everything.
    • Prefer blocks of 1–3 hours per topic per day.
    • A topic may appear on multiple days if more time is needed.

5.  NO EXAM DATE
    If a subject has no exam date, treat it as lowest priority and schedule it
    only when higher-priority subjects are fully covered for the day.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT FORMAT  (return ONLY this JSON, nothing else)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{{
  "DD-MM-YYYY": {{
    "SubjectName": {{
      "TopicName": <integer hours>
    }}
  }}
}}

• Omit any day with 0 available hours entirely.
• Omit any subject or topic that is not assigned hours on a given day.
• Do NOT include any explanation, markdown, or extra text.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INPUT DATA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{json.dumps(topic_data, ensure_ascii=False, indent=2)}
"""

    response = client.chat.completions.create(
        model=MODEL,
        temperature=0.2,
        max_tokens=3000,
        messages=[
            {
                "role": "system",
                "content": "You are a study schedule optimiser. Your only output is valid JSON — no prose, no markdown."
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
    )

    content = response.choices[0].message.content

    # Strip thinking blocks and markdown fences (qwen3 thinking model)
    import re
    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
    content = re.sub(r'```(?:json)?', '', content).strip()

    start = content.find("{")
    end   = content.rfind("}") + 1

    return json.loads(content[start:end])