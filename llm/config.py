import os
from openai import OpenAI

# Dynamic resolution of LLM API key with backwards-compatible local fallback
api_key = os.environ.get("AICREDITS_API_KEY")
if not api_key:
    try:
        import apikey
        api_key = apikey.key
    except ImportError:
        api_key = "placeholder-key-replace-me"

# Initialize the OpenAI client using the workspace-wide endpoint and API key
client = OpenAI(base_url="https://api.aicredits.in/v1", api_key=api_key)

# Default model candidates list.
# Order indicates priority for fallback execution.
MODEL_CANDIDATES = [
    "mistralai/mistral-nemo"
]
