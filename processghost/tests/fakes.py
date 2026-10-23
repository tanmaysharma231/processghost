"""Shared test doubles for core/pipeline.py's LLM call sites.

Moved out of tests/test_pipeline_fallback.py in v2 so tests/test_pipeline_writer.py (and, from
Phase 3 on, the Checker's per-flag call loop) can share one fake rather than each hand-rolling its
own. Extended backwards-compatibly -- every existing test_pipeline_fallback.py test keeps passing
unchanged.

No conftest.py fixture wrapper: the repo has none, and the hand-rolled class already reads well at
each call site (`_FakeClient(chat_response=...)`), so this keeps that convention rather than
introducing a new one partway through the test suite.
"""
from __future__ import annotations

from typing import Any, Callable, List, Union

from core.llm.client import Metrics

Response = Union[dict, Exception, Callable[..., dict]]


class _FakeClient:
    """Stands in for LLMClient/AnthropicClient.

    Each of chat_response/vision_response/structured_response may be:
    - a plain value (dict) -- returned as-is on every call,
    - an Exception instance -- raised on every call,
    - a callable -- invoked with the call's arguments, its return value or raised exception used,
    - a list -- consumed one item per call (each item may itself be any of the above); once
      exhausted, the last item repeats. This is what a Checker per-flag call loop (Phase 3) needs
      that a single canned response cannot express.

    chat_calls / vision_calls record the raw messages passed to each call (list[list[dict]]);
    call_order records the call sequence ("chat" / "vision" / "structured") so tests can assert
    ordering.
    """

    def __init__(
        self,
        chat_response: Any = None,
        vision_response: Any = None,
        structured_response: Any = None,
    ):
        self.metrics = Metrics()
        self._chat_response = chat_response
        self._vision_response = vision_response
        self._structured_response = structured_response
        self._chat_calls_made = 0
        self._vision_calls_made = 0
        self._structured_calls_made = 0
        self.chat_calls: List[list] = []
        self.vision_calls: List[list] = []
        self.structured_calls: List[tuple] = []
        self.call_order: List[str] = []

    @staticmethod
    def _resolve(response: Any, call_index: int, *args) -> Any:
        if isinstance(response, list):
            item = response[min(call_index, len(response) - 1)] if response else None
        else:
            item = response
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item(*args)
        return item

    def chat(self, messages, **kwargs):
        self.chat_calls.append(messages)
        self.call_order.append("chat")
        result = self._resolve(self._chat_response, self._chat_calls_made, messages)
        self._chat_calls_made += 1
        return result

    def vision(self, messages, **kwargs):
        self.vision_calls.append(messages)
        self.call_order.append("vision")
        result = self._resolve(self._vision_response, self._vision_calls_made, messages)
        self._vision_calls_made += 1
        return result

    def structured_chat(
        self, system_prompt, user_text, schema, *, stage="unknown", images=None, model=None, payload_extra=None
    ):
        self.structured_calls.append((system_prompt, user_text, schema, images))
        self.call_order.append("structured")
        result = self._resolve(self._structured_response, self._structured_calls_made, system_prompt, user_text, schema)
        self._structured_calls_made += 1
        return result


def openai_response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}
