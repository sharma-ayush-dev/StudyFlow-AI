"""
teacher.py — Study tutor LLM logic

Architecture:
- TEACHER_MODELS : fallback list tried in order (same pattern as schedule_planner)
- get_initial_message() : called once when a chat is first opened; AI proactively starts teaching
- get_reply()           : called on every user message; uses a 12-message sliding window
- get_quiz()            : called when user clicks "Quiz Me"; generates 3-5 questions from recent context

Token optimisation:
- System prompt is ~90 tokens in English, ~55 in Chinese (admin toggle)
- Only the last 12 messages (6 turns) are sent to the LLM; full history lives in DB
- Initial message and quiz share the same system prompt builder

Chinese system prompts:
- When enabled by admin, the SYSTEM prompt is sent in Chinese
- The LLM is explicitly instructed to ALWAYS REPLY IN ENGLISH
- Savings: ~30-40% on system prompt tokens per call
- Risk: inconsistent on weaker models — admin should test per-model before enabling
"""

import re
import json
from openai import OpenAI
import apikey

client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=apikey.key)

# Fallback list — tried top to bottom on any failure
TEACHER_MODELS = [
    "qwen/qwen2.5-72b-instruct",         # primary — fast, reliable, free tier friendly
    "google/gemini-2.0-flash-001",        # fallback 1
    "meta-llama/llama-4-maverick",        # fallback 2
]

SLIDING_WINDOW = 12   # max messages (not counting system prompt) sent per call


# ─────────────────────────────────────────────
# SYSTEM PROMPT BUILDERS
# ─────────────────────────────────────────────

def _build_system_prompt_en(course: str, subject: str, topic: str,
                             subtopics: list, hours: int | None) -> str:
    sub_str  = ", ".join(subtopics) if subtopics else "general concepts"
    hrs_str  = f"{hours}h allocated today" if hours else "open session"
    return (
        f"You are an expert study tutor. "
        f"Student's course: {course or 'not specified'}. "
        f"Subject: {subject}. Topic: {topic} ({hrs_str}). "
        f"Subtopics: {sub_str}.\n"
        f"Rules:\n"
        f"1. Proactively teach — start explaining without waiting for questions.\n"
        f"2. Use clear examples, analogies, and step-by-step breakdowns.\n"
        f"3. Build on the existing conversation — never repeat what is already covered.\n"
        f"4. Keep each response focused (200–400 words max) — do not dump everything at once.\n"
        f"5. When the user asks to be quizzed, generate 3-5 questions with answers hidden.\n"
        f"6. Always reply in English regardless of any other instruction."
    )


def _build_system_prompt_zh(course: str, subject: str, topic: str,
                             subtopics: list, hours: int | None) -> str:
    """Compact Chinese system prompt — ~40% fewer tokens than English version."""
    sub_str = "、".join(subtopics) if subtopics else "基本概念"
    hrs_str = f"今日{hours}小时" if hours else "开放课时"
    return (
        f"你是专业学习导师。"
        f"学生课程：{course or '未指定'}。"
        f"科目：{subject}，话题：{topic}（{hrs_str}）。"
        f"子话题：{sub_str}。\n"
        f"规则：1.主动教学，勿等提问。2.举例说明，循序渐进。"
        f"3.延续对话，勿重复。4.每次回复200-400词。"
        f"5.被要求测验时出3-5道题并隐藏答案。"
        f"6.所有回复必须用英语。"
    )


def build_system_prompt(course: str, subject: str, topic: str,
                         subtopics: list, hours: int | None,
                         use_chinese: bool = False) -> str:
    if use_chinese:
        return _build_system_prompt_zh(course, subject, topic, subtopics, hours)
    return _build_system_prompt_en(course, subject, topic, subtopics, hours)


# ─────────────────────────────────────────────
# INTERNAL: CALL ONE MODEL WITH FALLBACK
# ─────────────────────────────────────────────

def _call(messages: list, model_list: list) -> tuple[str, str, list]:
    """
    Returns (content, model_used, failure_reasons).
    Raises RuntimeError if all models fail.
    """
    failures = []
    for i, model in enumerate(model_list):
        try:
            print(f"[TEACHER] Trying {i+1}/{len(model_list)}: {model}")
            resp   = client.chat.completions.create(
                model=model, temperature=0.7, max_tokens=600, messages=messages)
            choice = resp.choices[0] if resp.choices else None
            if not choice or not choice.message.content:
                raise ValueError(f"Empty response (finish_reason={getattr(choice,'finish_reason','?')})")
            return choice.message.content.strip(), model, failures
        except Exception as exc:
            reason = f"[{model}] {type(exc).__name__}: {exc}"
            print(f"[ERROR] {reason}")
            failures.append(reason)
    raise RuntimeError("All teacher models failed:\n" + "\n".join(failures))


# ─────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────

def get_initial_message(course: str, subject: str, topic: str,
                         subtopics: list, hours: int | None,
                         model_list: list = None,
                         use_chinese: bool = False) -> tuple[str, str, list]:
    """
    Generate the first proactive teaching message for a new chat.
    Returns (content, model_used, failure_reasons).
    """
    if model_list is None:
        model_list = TEACHER_MODELS

    system = build_system_prompt(course, subject, topic, subtopics, hours, use_chinese)

    sub_str = ", ".join(subtopics[:3]) if subtopics else topic
    trigger = (
        f"I want to study {topic} today. "
        f"I have {hours or 'some'} hours. Please start teaching me."
    )

    messages = [
        {"role": "system",    "content": system},
        {"role": "user",      "content": trigger},
    ]

    return _call(messages, model_list)


def get_reply(history: list, new_message: str,
              course: str, subject: str, topic: str,
              subtopics: list, hours: int | None,
              model_list: list = None,
              use_chinese: bool = False) -> tuple[str, str, list]:
    """
    Get a reply to the user's message.

    history : list of {"role": "user"|"assistant", "content": "..."}
              — the FULL stored history; we apply the sliding window here.
    Returns (content, model_used, failure_reasons).
    """
    if model_list is None:
        model_list = TEACHER_MODELS

    system  = build_system_prompt(course, subject, topic, subtopics, hours, use_chinese)
    # Apply sliding window — keep last SLIDING_WINDOW messages
    window  = history[-SLIDING_WINDOW:] if len(history) > SLIDING_WINDOW else history

    messages = [{"role": "system", "content": system}] + window + [
        {"role": "user", "content": new_message}
    ]

    return _call(messages, model_list)


def get_quiz(history: list,
             course: str, subject: str, topic: str,
             subtopics: list, hours: int | None,
             model_list: list = None,
             use_chinese: bool = False) -> tuple[str, str, list]:
    """
    Generate a quiz based on what has been taught so far.
    Uses the last 8 messages as context (enough to see what's been covered).
    Returns (content, model_used, failure_reasons).
    """
    if model_list is None:
        model_list = TEACHER_MODELS

    system = build_system_prompt(course, subject, topic, subtopics, hours, use_chinese)
    window = history[-8:] if len(history) > 8 else history

    quiz_trigger = (
        "Quiz me on everything we have covered so far. "
        "Generate 3-5 questions of increasing difficulty. "
        "Show the questions but hide the answers — I will answer first."
    )

    messages = [{"role": "system", "content": system}] + window + [
        {"role": "user", "content": quiz_trigger}
    ]

    return _call(messages, model_list)