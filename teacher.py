"""
teacher.py — Study tutor LLM logic

Changes vs original:
  - FALLBACK_WINDOWS reduced from [12,8,4,0] to [12,4] — cuts worst-case
    retry latency by 50% while preserving context-shrink capability
  - Added early non-context error detection (skip remaining windows immediately)
  - max_tokens kept at 800 (adequate for 200-400 word responses per rules)
"""

import re
from openai import OpenAI
import apikey

client = OpenAI(base_url="https://api.doubleword.ai/v1", api_key=apikey.key)

TEACHER_MODELS = [
    "openai/gpt-oss-20b"
]

SLIDING_WINDOW   = 12
# Reduced from [12,8,4,0] — 2 steps is sufficient: full context then half context.
# The "0" fallback (no history) produces low-quality responses and adds latency.
FALLBACK_WINDOWS = [12, 4]

_CONTEXT_ERRORS = (
    'context_length_exceeded', 'context window', 'maximum context',
    'too many tokens', 'input is too long', 'context_length'
)


# ─────────────────────────────────────────────
# SYSTEM PROMPT BUILDERS
# ─────────────────────────────────────────────

def _build_system_prompt_en(course, subject, topic, subtopics, hours):
    sub_str = ", ".join(subtopics) if subtopics else "general concepts"
    hrs_str = f"{hours}h allocated today" if hours else "open session"
    return (
        f"You are an expert study tutor in a 1-on-1 session.\n"
        f"Student course: {course or 'not specified'}. "
        f"Subject: {subject}. Topic: {topic} ({hrs_str}).\n"
        f"Subtopics to cover: {sub_str}.\n\n"

        f"CONVERSATION ROLES (critical):\n"
        f"- role='assistant' messages = YOUR previous explanations.\n"
        f"- role='user' messages = questions FROM THE STUDENT.\n"
        f"- Never confuse the two. Never repeat what you already explained.\n\n"

        f"STUDY-ONLY RESTRICTION (strictly enforced):\n"
        f"- You ONLY discuss topics directly related to {subject} — {topic}.\n"
        f"- If asked ANYTHING unrelated to studying this subject, politely decline "
        f"and redirect: 'I'm your study tutor for {topic}. Let's stay focused!'\n"
        f"- You are NOT a general-purpose chatbot. Never assist with other tasks.\n\n"

        f"TEACHING RULES:\n"
        f"1. Proactively teach — explain without waiting for questions.\n"
        f"2. Use concrete examples, analogies, step-by-step breakdowns.\n"
        f"3. Build on previous messages — go deeper, never repeat.\n"
        f"4. Keep responses 200–400 words. One concept at a time.\n"
        f"5. End EVERY response with: '**Next up:** [brief hint of what you'll cover next]'\n"
        f"6. Always reply in English.\n\n"

        f"QUIZ FORMAT (only when explicitly asked):\n"
        f"7. Generate 3–5 questions of increasing difficulty.\n"
        f"8. Wrap EACH answer: [ANS] answer here [/ANS]\n"
        f"9. Format: Q1. question\\n[ANS] answer [/ANS]\\nQ2. question\\n[ANS] answer [/ANS]\n"
        f"10. Never put two questions before their answers."
    )


def _build_system_prompt_zh(course, subject, topic, subtopics, hours):
    sub_str = "、".join(subtopics) if subtopics else "基本概念"
    hrs_str = f"今日{hours}小时" if hours else "开放课时"
    return (
        f"你是专业一对一学习导师。学生课程：{course or '未指定'}。"
        f"科目：{subject}，话题：{topic}（{hrs_str}）。需涵盖：{sub_str}。\n\n"
        f"角色规则：role='assistant'=你的回复；role='user'=学生消息。切勿混淆，切勿重复。\n\n"
        f"限制：只讨论{subject}-{topic}相关内容。拒绝无关请求并礼貌重定向。\n\n"
        f"教学：1.主动讲解。2.举例循序渐进。3.延续深入勿重复。"
        f"4.每次200-400词。5.结尾必须写'**Next up:** [下一步简介]'。6.必须用英语回复。\n\n"
        f"测验：出3-5道题。答案格式：[ANS]答案[/ANS]。Q后立即接ANS，不得所有Q在前。"
    )


def build_system_prompt(course, subject, topic, subtopics, hours, use_chinese=False):
    if use_chinese:
        return _build_system_prompt_zh(course, subject, topic, subtopics, hours)
    return _build_system_prompt_en(course, subject, topic, subtopics, hours)


# ─────────────────────────────────────────────
# CONTEXT FALLBACK HELPER
# ─────────────────────────────────────────────

def _is_context_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(kw in msg for kw in _CONTEXT_ERRORS)


def _call_with_fallback(messages_fn, model_list, is_streaming=False):
    """
    Try each model. On context-length error only, shrink the window and retry.
    On any other error, move immediately to the next model (no window retry).
    Returns (content_or_generator, model_used, failures)
    """
    failures = []

    for i, model in enumerate(model_list):
        for window in FALLBACK_WINDOWS:
            msgs = messages_fn(window)
            try:
                print(f"[TEACHER] {model} window={window}")
                if is_streaming:
                    resp = client.chat.completions.create(
                        model=model, temperature=0.7, max_tokens=800,
                        messages=msgs, stream=True)

                    def _gen(r=resp):
                        for chunk in r:
                            delta = chunk.choices[0].delta if chunk.choices else None
                            if delta and delta.content:
                                yield delta.content

                    return _gen(), model, failures

                else:
                    resp   = client.chat.completions.create(
                        model=model, temperature=0.7, max_tokens=800, messages=msgs)
                    choice = resp.choices[0] if resp.choices else None
                    if not choice or not choice.message.content:
                        raise ValueError("Empty response")
                    return choice.message.content.strip(), model, failures

            except Exception as exc:
                reason = f"[{model} w={window}] {type(exc).__name__}: {exc}"
                if _is_context_error(exc):
                    print(f"[CONTEXT TOO LARGE] Shrinking window from {window}…")
                    continue   # try smaller window, same model
                # Non-context error — don't waste time with smaller windows
                print(f"[ERROR] {reason}")
                failures.append(reason)
                break   # move to next model

    raise RuntimeError("All teacher models failed:\n" + "\n".join(failures))


# ─────────────────────────────────────────────
# MESSAGE BUILDERS
# ─────────────────────────────────────────────

def _build_reply_messages(history, new_message, system):
    def _fn(window):
        w = history[-window:] if window and len(history) > window else history
        return [{"role": "system", "content": system}] + w + [
            {"role": "user", "content": new_message}
        ]
    return _fn


def _build_quiz_messages(history, system):
    quiz_trigger = (
        "Quiz me on everything we have covered so far in this session.\n"
        "Generate 3-5 questions of increasing difficulty.\n"
        "MANDATORY FORMAT for each question:\n"
        "Q1. [question]\n[ANS] [answer] [/ANS]\n\n"
        "Q2. [question]\n[ANS] [answer] [/ANS]\n\n"
        "Do NOT list all questions first. Each [ANS][/ANS] must immediately follow its question."
    )

    def _fn(window):
        w = history[-window:] if window and len(history) > window else history
        return [{"role": "system", "content": system}] + w + [
            {"role": "user", "content": quiz_trigger}
        ]
    return _fn


def _build_initial_messages(trigger, system):
    def _fn(window):   # window ignored for initial message
        return [
            {"role": "system", "content": system},
            {"role": "user",   "content": trigger}
        ]
    return _fn


# ─────────────────────────────────────────────
# PUBLIC API — NON-STREAMING
# ─────────────────────────────────────────────

def get_initial_message(course, subject, topic, subtopics, hours,
                        model_list=None, use_chinese=False):
    if model_list is None: model_list = TEACHER_MODELS
    system  = build_system_prompt(course, subject, topic, subtopics, hours, use_chinese)
    trigger = (f"I want to study {topic} today. I have {hours or 'some'} hours. "
               f"Please start teaching me the first subtopic.")
    return _call_with_fallback(_build_initial_messages(trigger, system), model_list)


def get_reply(history, new_message, course, subject, topic,
              subtopics, hours, model_list=None, use_chinese=False):
    if model_list is None: model_list = TEACHER_MODELS
    system = build_system_prompt(course, subject, topic, subtopics, hours, use_chinese)
    return _call_with_fallback(
        _build_reply_messages(history, new_message, system), model_list)


def get_quiz(history, course, subject, topic, subtopics, hours,
             model_list=None, use_chinese=False):
    if model_list is None: model_list = TEACHER_MODELS
    system = build_system_prompt(course, subject, topic, subtopics, hours, use_chinese)
    return _call_with_fallback(_build_quiz_messages(history, system), model_list)


# ─────────────────────────────────────────────
# PUBLIC API — STREAMING GENERATORS
# ─────────────────────────────────────────────

def stream_reply(history, new_message, course, subject, topic,
                 subtopics, hours, model_list=None, use_chinese=False):
    """Yields text chunks. Raises RuntimeError if all models fail."""
    if model_list is None: model_list = TEACHER_MODELS
    system = build_system_prompt(course, subject, topic, subtopics, hours, use_chinese)
    gen, _, _ = _call_with_fallback(
        _build_reply_messages(history, new_message, system),
        model_list, is_streaming=True)
    yield from gen


def stream_quiz(history, course, subject, topic, subtopics, hours,
                model_list=None, use_chinese=False):
    """Yields text chunks for quiz. Raises RuntimeError if all models fail."""
    if model_list is None: model_list = TEACHER_MODELS
    system = build_system_prompt(course, subject, topic, subtopics, hours, use_chinese)
    gen, _, _ = _call_with_fallback(
        _build_quiz_messages(history, system),
        model_list, is_streaming=True)
    yield from gen