"""The model boundary, behind an injectable Protocol so the pipeline runs offline.

The OpenAI client is called directly rather than through ai.responses because usage
lives on the response, which ai.responses discards; the response is handed to their
_log_usage untouched and also recorded here.

Cost is computed independently because reasoning_tokens is a subset of output_tokens,
so pricing both overstates a call by roughly 1.9x. The README notes the discrepancy.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

import ai

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


@dataclasses.dataclass(frozen=True)
class UsageRecord:
    """What one model call consumed."""

    model: str
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cost_usd: float


# Process-wide total for the run summary; per-product cost is a slice of one
# client's own records.
USAGE_TOTAL = {
    "calls": 0,
    "input_tokens": 0,
    "output_tokens": 0,
    "reasoning_tokens": 0,
    "cost_usd": 0.0,
}


def reset_usage_total() -> None:
    """Zero the process-wide total. For tests and for a fresh ingest run."""
    for key in USAGE_TOTAL:
        USAGE_TOTAL[key] = 0 if key != "cost_usd" else 0.0


def cost_of(model: str, input_tokens: int, output_tokens: int) -> float:
    """Work out what one model call cost, in dollars.

    A model we have no price for costs zero rather than crashing a whole batch. A test
    checks every model in config.py is priced, so a missing one fails there instead.
    """
    prices = ai.MODEL_PRICES.get(model, {"input": 0.0, "output": 0.0})
    return (input_tokens / 1_000_000) * prices["input"] + (
        output_tokens / 1_000_000
    ) * prices["output"]


class LLMClient(Protocol):
    """Minimal surface the extraction pipeline needs from a model."""

    records: list[UsageRecord]

    async def parse(
        self,
        model: str,
        input: list[dict[str, Any]],
        text_format: type[T],
        **kwargs: Any,
    ) -> T | None:
        """Return the parsed structured output, or None on refusal."""
        ...


class _RecordingClient:
    """Shared usage bookkeeping."""

    def __init__(self) -> None:
        self.records: list[UsageRecord] = []

    def _add(self, record: UsageRecord) -> None:
        """Record one call's usage, both on this client and in the run-wide total."""
        self.records.append(record)
        USAGE_TOTAL["calls"] += 1
        USAGE_TOTAL["input_tokens"] += record.input_tokens
        USAGE_TOTAL["output_tokens"] += record.output_tokens
        USAGE_TOTAL["reasoning_tokens"] += record.reasoning_tokens
        USAGE_TOTAL["cost_usd"] += record.cost_usd

    def cost_since(self, index: int) -> float:
        """Total cost of every call made since `index`, which is one product's worth."""
        return sum(record.cost_usd for record in self.records[index:])


class OpenRouterClient(_RecordingClient):
    """Live client. Holds the response so usage can be both logged and recorded."""

    async def parse(
        self,
        model: str,
        input: list[dict[str, Any]],
        text_format: type[T],
        **kwargs: Any,
    ) -> T | None:
        """Make one real API call, log its usage, and return the parsed answer."""
        # Their cached client keeps key handling and base_url in one place;
        # constructing our own would duplicate their env-var validation.
        client = ai._get_client()

        response = await client.responses.parse(
            model=model,
            input=input,
            text_format=text_format,
            **kwargs,
        )

        ai._log_usage(response)
        self._record(response)

        return response.output_parsed

    def _record(self, response: Any) -> None:
        """Pull the token counts off a response and store them with the cost."""
        usage = getattr(response, "usage", None)
        if usage is None:
            logger.warning("No usage data on response; cost will be understated")
            return

        model = getattr(response, "model", "unknown")
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0

        reasoning_tokens = 0
        details = getattr(usage, "output_tokens_details", None)
        if details is not None:
            reasoning_tokens = getattr(details, "reasoning_tokens", 0) or 0

        self._add(
            UsageRecord(
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                cost_usd=cost_of(model, input_tokens, output_tokens),
            )
        )


class FakeLLMClient(_RecordingClient):
    """Returns queued responses and remembers what it was asked.

    A queue entry may be a model instance, None for a refusal, or an exception to
    raise, which covers every branch the repair logic handles.
    """

    def __init__(self, responses: list[Any] | None = None) -> None:
        super().__init__()
        self.queue: list[Any] = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    async def parse(
        self,
        model: str,
        input: list[dict[str, Any]],
        text_format: type[T],
        **kwargs: Any,
    ) -> T | None:
        """Return the next queued answer, recording what was asked. No network call."""
        self.calls.append(
            {
                "model": model,
                "input": input,
                "text_format": text_format,
                "kwargs": kwargs,
            }
        )
        self._add(
            UsageRecord(
                model=model,
                input_tokens=0,
                output_tokens=0,
                reasoning_tokens=0,
                cost_usd=0.0,
            )
        )

        if not self.queue:
            raise AssertionError(
                f"FakeLLMClient has no queued response for call "
                f"{len(self.calls)} ({text_format.__name__})"
            )

        nxt = self.queue.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt
