from openai import OpenAI

from app.config import settings


def get_client() -> OpenAI:
    """Points at Ollama's OpenAI-compatible endpoint. Swap base_url to move to OpenVINO Model Server."""
    return OpenAI(base_url=settings.ollama_base_url, api_key="ollama")
