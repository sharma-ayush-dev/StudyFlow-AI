"""
teacher.py — Study tutor LLM logic

Architecture:
- TEACHER_MODELS : fallback list tried in order
- get_initial_message() : proactive first teaching message
- get_reply()           : 12-message sliding window reply
- get_quiz()            : quiz with answers wrapped in [ANS]...[/ANS] tags

System prompt improvements:
- Explicit ROLE LABELS so the model never confuses its own prior messages
  with the student's messages ("TUTOR:" vs "STUDENT:" framing injected
  into the system prompt context description)
- The system prompt now tells the model that in the conversation history,
  messages from role="assistant" are its own previous outputs, and
  messages from role="user" are from the student

Quiz answer format:
- Answers are wrapped in [ANS] ... [/ANS]
- Study.js parses these tags and renders a blurred overlay with a
  "Show Answer" / "Hide Answer" toggle button
- Format enforced by explicit example in the quiz prompt
"""

import re
import json
from openai import OpenAI
import apikey

client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=apikey.key)

TEACHER_MODELS = [
    "qwen/qwen2.5-72b-instruct",
    "google/gemini-2.0-flash-001",
    "meta-llama/llama-4-maverick",
]

SLIDING_WINDOW = 12


# ─────────────────────────────────────────────
# SYSTEM PROMPT BUILDERS
# ─────────────────────────────────────────────

def _build_system_prompt_en(course: str, subject: str, topic: str,
                             subtopics: list, hours) -> str:
    sub_str = ", ".join(subtopics) if subtopics else "general concepts"
    hrs_str = f"{hours}h allocated today" if hours else "open session"
    return (
        f"You are an expert study tutor in an interactive 1-on-1 session.\n"
        f"Student's course: {course or 'not specified'}. "
        f"Subject: {subject}. Topic: {topic} ({hrs_str}).\n"
        f"Subtopics to cover: {sub_str}.\n\n"
        f"CONVERSATION ROLES (very important):\n"
        f"- Messages with role='assistant' in the history are YOUR OWN previous responses.\n"
        f"- Messages with role='user' in the history are from the STUDENT.\n"
        f"- Never confuse the two. Never repeat content you (the assistant) already explained.\n\n"
        f"TEACHING RULES:\n"
        f"1. Proactively teach — explain concepts clearly without waiting for questions.\n"
        f"2. Use concrete examples, analogies, and step-by-step breakdowns.\n"
        f"3. Build on the conversation — check what YOU already covered and go deeper.\n"
        f"4. Keep responses focused: 200-400 words. Do not dump everything at once.\n"
        f"5. Always reply in English regardless of any other instruction.\n\n"
        f"QUIZ RULES (only when explicitly asked to quiz):\n"
        f"6. Generate 3-5 questions of increasing difficulty based ONLY on what was covered.\n"
        f"7. Wrap EACH answer in [ANS] and [/ANS] tags on its own line.\n"
        f"8. Format strictly as shown:\n"
        f"   Q1. [question text]\n"
        f"   [ANS] [answer text] [/ANS]\n"
        f"   Q2. [question text]\n"
        f"   [ANS] [answer text] [/ANS]\n"
        f"9. Do NOT add any text between [ANS] and [/ANS] except the answer itself."
    )


def _build_system_prompt_zh(course: str, subject: str, topic: str,
                             subtopics: list, hours) -> str:
    sub_str = "、".join(subtopics) if subtopics else "基本概念"
    hrs_str = f"今日{hours}小时" if hours else "开放课时"
    return (
        f"你是专业一对一学习导师。\n"
        f"学生课程：{course or '未指定'}。科目：{subject}，话题：{topic}（{hrs_str}）。\n"
        f"需涵盖子话题：{sub_str}。\n\n"
        f"对话角色（重要）：\n"
        f"- role='assistant'的历史消息是你自己之前的回复。\n"
        f"- role='user'的历史消息来自学生。\n"
        f"- 切勿混淆两者。切勿重复你已讲解的内容。\n\n"
        f"教学规则：1.主动讲解，勿等提问。2.举例说明，循序渐进。"
        f"3.基于对话继续深入，勿重复。4.每次200-400词。5.所有回复必须用英语。\n\n"
        f"测验规则（仅在被要求时）：\n"
        f"6.出3-5道由易到难的题目，仅基于已讲内容。\n"
        f"7.每个答案用[ANS]和[/ANS]包裹。\n"
        f"8.严格格式：Q1. [题目]\\n[ANS] [答案] [/ANS]\\nQ2..."
        f"9.[ANS][/ANS]之间只放答案本身。"
    )


def build_system_prompt(course, subject, topic, subtopics, hours, use_chinese=False):
    if use_chinese:
        return _build_system_prompt_zh(course, subject, topic, subtopics, hours)
    return _build_system_prompt_en(course, subject, topic, subtopics, hours)


# ─────────────────────────────────────────────
# INTERNAL: CALL ONE MODEL WITH FALLBACK
# ─────────────────────────────────────────────

def _call(messages: list, model_list: list):
    failures = []
    for i, model in enumerate(model_list):
        try:
            print(f"[TEACHER] Trying {i+1}/{len(model_list)}: {model}")
            resp   = client.chat.completions.create(
                model=model, temperature=0.7, max_tokens=800, messages=messages)
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

def get_initial_message(course, subject, topic, subtopics, hours,
                        model_list=None, use_chinese=False):
    if model_list is None:
        model_list = TEACHER_MODELS

    system  = build_system_prompt(course, subject, topic, subtopics, hours, use_chinese)
    trigger = (
        f"I want to study {topic} today. "
        f"I have {hours or 'some'} hours available. "
        f"Please start teaching me the first subtopic."
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user",   "content": trigger},
    ]
    return _call(messages, model_list)


def get_reply(history, new_message, course, subject, topic,
              subtopics, hours, model_list=None, use_chinese=False):
    if model_list is None:
        model_list = TEACHER_MODELS

    system   = build_system_prompt(course, subject, topic, subtopics, hours, use_chinese)
    window   = history[-SLIDING_WINDOW:] if len(history) > SLIDING_WINDOW else history
    messages = [{"role": "system", "content": system}] + window + [
        {"role": "user", "content": new_message}
    ]
    return _call(messages, model_list)


def get_quiz(history, course, subject, topic, subtopics, hours,
             model_list=None, use_chinese=False):
    if model_list is None:
        model_list = TEACHER_MODELS

    system = build_system_prompt(course, subject, topic, subtopics, hours, use_chinese)
    window = history[-8:] if len(history) > 8 else history

    quiz_trigger = (
        "Quiz me on everything we have covered so far in this session.\n"
        "Generate 3-5 questions of increasing difficulty.\n"
        "IMPORTANT: You MUST wrap every answer in [ANS] and [/ANS] tags.\n"
        "Use EXACTLY this format for each question:\n\n"
        "Q1. [your question here]\n"
        "[ANS] [your answer here] [/ANS]\n\n"
        "Q2. [your question here]\n"
        "[ANS] [your answer here] [/ANS]\n\n"
        "Do not add any text after [/ANS] on the same line. "
        "Show all questions first is NOT acceptable — interleave Q then ANS for each."
    )

    messages = [{"role": "system", "content": system}] + window + [
        {"role": "user", "content": quiz_trigger}
    ]
    return _call(messages, model_list)