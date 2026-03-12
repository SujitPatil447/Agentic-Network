"""Portkey-backed LLM factory for LangChain agents (Anthropic via Portkey)."""

from langchain_openai import ChatOpenAI
from portkey_ai import createHeaders

from src.config import settings


def get_llm(temperature: float = 0.0) -> ChatOpenAI:
    """Create a ChatOpenAI instance routed through Portkey to Anthropic.

    Portkey acts as a proxy — we use the OpenAI-compatible client pointed at
    Portkey's gateway, which translates requests to Anthropic's API based on
    the virtual key configured in the Portkey dashboard.

    To switch providers later, just update the virtual key and model name.
    """
    headers = createHeaders(
        api_key=settings.portkey_api_key,
    )

    return ChatOpenAI(
        api_key=settings.portkey_api_key,
        base_url=settings.portkey_base_url,
        default_headers=headers,
        model=settings.model_name,
        temperature=temperature,
        max_tokens=4096,
    )
