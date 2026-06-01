import logging
import datetime
from llm.config import client, MODEL_CANDIDATES
from llm.extraction_prompt import _PROMPT
from llm.parser import _extract_json
from llm.validators import _normalize, _filter_dates, _ensure_study_days

logger = logging.getLogger(__name__)

def understand_document(
    clean_text: str,
    today_str: str = None,
    model_list: list = None,
    max_output_tokens: int = 4000,
    user_id: int = None
) -> dict:
    """
    Takes clean extracted text and runs it through the LLM understanding layer.
    Outputs a normalized JSON payload containing Subjects, Exam_dates, and study_days.
    """
    if today_str is None:
        today_str = datetime.date.today().strftime('%d-%m-%Y')
    if model_list is None:
        model_list = MODEL_CANDIDATES

    # ── Check cost limit before LLM call ──────────────────────
    if user_id:
        from helpers import check_user_cost_limit
        if not check_user_cost_limit(user_id):
            raise ValueError("Cost limit exceeded. Please contact the administrator.")

    # Format content as block objects to match original API payload style
    content = [
        {"type": "text", "text": clean_text},
        {"type": "text", "text": _PROMPT}
    ]
    messages = [{'role': 'user', 'content': content}]
    failures = []

    for i, model in enumerate(model_list):
        try:
            logger.info(f"Running LLM understanding with {model} ({i+1}/{len(model_list)})")
            resp = client.chat.completions.create(
                model=model,
                temperature=0,
                max_tokens=max_output_tokens,
                messages=messages
            )
            choice = resp.choices[0] if resp.choices else None
            if not choice or not choice.message.content:
                raise ValueError("Empty response from LLM")
            
            # Track usage
            if user_id:
                prompt_tokens = getattr(resp.usage, 'prompt_tokens', 0) if (hasattr(resp, 'usage') and resp.usage) else 0
                completion_tokens = getattr(resp.usage, 'completion_tokens', 0) if (hasattr(resp, 'usage') and resp.usage) else 0
                if not prompt_tokens:
                    prompt_tokens = max(1, len(clean_text) // 4)
                if not completion_tokens:
                    completion_tokens = max(1, len(choice.message.content) // 4)
                
                from helpers import track_llm_call
                track_llm_call(user_id, model, prompt_tokens, completion_tokens)

            # Clean and extract JSON object
            parsed = _extract_json(choice.message.content)
            
            # Normalize and filter schema
            normed = _normalize(parsed)
            normed = _filter_dates(normed)
            normed = _ensure_study_days(normed, today_str)
            
            return normed
        except Exception as e:
            reason = f"[{model}] {type(e).__name__}: {e}"
            logger.error(reason)
            failures.append(reason)

    raise RuntimeError("All LLM understanding models failed:\n" + "\n".join(failures))
