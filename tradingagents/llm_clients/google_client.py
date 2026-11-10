import logging
import re
import time
from typing import Any

from langchain_google_genai import ChatGoogleGenerativeAI

from .base_client import BaseLLMClient, normalize_content
from .validators import validate_model

logger = logging.getLogger(__name__)

_GEMINI_VERSION = re.compile(r"^gemini-(\d+)\.(\d+)")


def _accepts_minimal_thinking(model: str) -> bool:
    """Whether ``thinking_level="minimal"`` is accepted: numbered Flash models
    before 3.8. Pro, 3.8+ and version-less aliases (which move between
    generations) are treated as rejecting it."""
    model_lc = model.lower()
    match = _GEMINI_VERSION.match(model_lc)
    return bool(match) and "pro" not in model_lc and (
        (int(match.group(1)), int(match.group(2))) < (3, 8)
    )


class NormalizedChatGoogleGenerativeAI(ChatGoogleGenerativeAI):
    """ChatGoogleGenerativeAI with normalized content output and resilient retry.

    Gemini 3 models return content as list of typed blocks.
    This normalizes to string for consistent downstream handling.
    Automatically retries transient 503 UNAVAILABLE (high demand spikes)
    and 429 RESOURCE_EXHAUSTED errors with exponential backoff.
    """

    def invoke(self, input, config=None, **kwargs):
        max_attempts = 5
        base_delay = 3
        fallback_models = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
        for attempt in range(1, max_attempts + 1):
            try:
                return normalize_content(super().invoke(input, config, **kwargs))
            except Exception as e:
                err_str = str(e).lower()

                # Handle daily free-tier quota limit (e.g. 20 requests/day on preview models)
                is_quota_exhausted = (
                    "quota exceeded" in err_str
                    or "limit exceeded on free tier" in err_str
                    or "requests per day limit exceeded" in err_str
                )
                if is_quota_exhausted and fallback_models:
                    next_model = fallback_models.pop(0)
                    if next_model != getattr(self, "model", ""):
                        logger.warning(
                            "Model %s hit daily quota limit (%s). Automatically falling back to %s...",
                            getattr(self, "model", "unknown"),
                            e,
                            next_model,
                        )
                        self.model = next_model
                        time.sleep(1)
                        continue

                # Handle transient 503 or momentary 429
                is_transient = any(
                    code in err_str
                    for code in (
                        "503",
                        "unavailable",
                        "429",
                        "resource_exhausted",
                        "high demand",
                        "rate limit",
                        "deadline exceeded",
                        "connection reset",
                    )
                )
                if is_transient and not is_quota_exhausted and attempt < max_attempts:
                    sleep_time = min(base_delay * (2 ** (attempt - 1)), 20)
                    logger.warning(
                        "Gemini API transient error (%s). Retrying in %ss (attempt %s/%s)...",
                        e,
                        sleep_time,
                        attempt,
                        max_attempts,
                    )
                    time.sleep(sleep_time)
                    continue
                raise


# The read timeout the OpenAI and Anthropic SDKs use by default.
REQUEST_TIMEOUT_SECONDS = 600


class GoogleClient(BaseLLMClient):
    """Client for Google Gemini models."""

    def __init__(self, model: str, base_url: str | None = None, **kwargs):
        super().__init__(model, base_url, **kwargs)

    def get_llm(self) -> Any:
        """Return configured ChatGoogleGenerativeAI instance."""
        self.warn_if_unknown_model()
        llm_kwargs = {"model": self.model}

        if self.base_url:
            llm_kwargs["base_url"] = self.base_url

        for key in ("timeout", "max_retries", "temperature", "max_output_tokens",
                    "callbacks", "http_client", "http_async_client"):
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]
        # Without a timeout a stalled Gemini call waits forever; the OpenAI and
        # Anthropic SDKs give up after the same 600 seconds (#1417).
        llm_kwargs.setdefault("timeout", REQUEST_TIMEOUT_SECONDS)

        # Unified api_key maps to provider-specific google_api_key
        google_api_key = self.kwargs.get("api_key") or self.kwargs.get("google_api_key")
        if google_api_key:
            llm_kwargs["google_api_key"] = google_api_key

        # Gemini 3.x takes the string ``thinking_level`` (the integer
        # ``thinking_budget`` was for the now-retired 2.5 line). Pro, Gemini
        # 3.8+ and the -latest aliases reject "minimal" with a 400; "low" is
        # accepted everywhere, so it is the fallback.
        thinking_level = self.kwargs.get("thinking_level")
        if thinking_level:
            if thinking_level == "minimal" and not _accepts_minimal_thinking(self.model):
                thinking_level = "low"
            llm_kwargs["thinking_level"] = thinking_level

        return NormalizedChatGoogleGenerativeAI(**llm_kwargs)

    def validate_model(self) -> bool:
        """Validate model for Google."""
        return validate_model("google", self.model)
