import os
import apikey
from openai import OpenAI

# Initialize the OpenAI client using the workspace-wide endpoint and API key
client = OpenAI(base_url="https://api.aicredits.in/v1", api_key=apikey.key)

# Default model candidates list.
# Order indicates priority for fallback execution.
MODEL_CANDIDATES = [
    "mistralai/mistral-nemo"
]
