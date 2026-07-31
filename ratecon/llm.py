"""LLM adapters behind a two-method protocol.

Default is OpenAI's ``json_schema`` with ``strict: true``, because it is a
grammar-level guarantee: the response cannot be syntactically invalid or
carry unknown keys. That removes one whole class of failure from a test
about schema enforcement.

Anthropic tool-use is a first-class alternative and is implemented here --
forcing a single tool gives an equivalent guarantee. Either way the
guarantee is about *shape*, not truth, which is why grounding.py and
project.py exist.

ReplayClient is the offline adapter: it serves recorded model output from
disk so the deterministic two-thirds of the pipeline can be tested in CI
with no API key and no spend.
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any, Protocol


class LLMClient(Protocol):
    name: str

    def complete_json(self, system: str, user: str, schema: dict,
                      schema_name: str) -> dict:
        """Return a parsed JSON object conforming to ``schema``."""


# --------------------------------------------------------------------------

def strictify(schema: dict) -> dict:
    """Make a Pydantic JSON schema acceptable to OpenAI strict mode.

    Strict mode requires every property to be listed in ``required`` and
    ``additionalProperties: false`` on every object. Optional fields stay
    nullable via their existing ``anyOf`` with null.
    """
    s = copy.deepcopy(schema)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            node.pop("default", None)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(s)
    return s


# --------------------------------------------------------------------------

class OpenAIClient:
    name = "openai"

    def __init__(self, model: str | None = None) -> None:
        from openai import OpenAI  # imported lazily so offline tests need no dep

        self.model = model or os.environ.get("RATECON_OPENAI_MODEL", "gpt-4.1")
        self._client = OpenAI()

    def complete_json(self, system: str, user: str, schema: dict,
                      schema_name: str) -> dict:
        resp = self._client.chat.completions.create(
            model=self.model,
            temperature=0,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": strictify(schema),
                },
            },
        )
        return json.loads(resp.choices[0].message.content)


class AnthropicClient:
    name = "anthropic"

    def __init__(self, model: str | None = None) -> None:
        import anthropic  # imported lazily

        # Model is config-driven on purpose; pin a dated snapshot in
        # deployment so a provider-side update cannot change behaviour
        # silently. See README, drift section.
        self.model = model or os.environ.get("RATECON_ANTHROPIC_MODEL",
                                             "claude-sonnet-4-5")
        self._client = anthropic.Anthropic()

    def complete_json(self, system: str, user: str, schema: dict,
                      schema_name: str) -> dict:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=4096,
            temperature=0,
            system=system,
            tools=[{
                "name": schema_name,
                "description": "Return the located fields.",
                "input_schema": schema,
            }],
            tool_choice={"type": "tool", "name": schema_name},
            messages=[{"role": "user", "content": user}],
        )
        for block in resp.content:
            if block.type == "tool_use":
                return dict(block.input)
        raise RuntimeError("model returned no tool_use block")


def deepseek_request_kwargs(model: str, system: str, user: str, schema: dict,
                            schema_name: str, thinking_type: str,
                            reasoning_effort: str) -> dict:
    """Build the chat-completions kwargs for a DeepSeek call.

    Kept as a pure function so the request shape -- model, forced tool, and
    the thinking/reasoning-effort object -- is unit-testable without the
    ``openai`` SDK installed. ``thinking`` rides in ``extra_body`` because it
    is a DeepSeek extension, not an OpenAI field; the OpenAI SDK merges
    ``extra_body`` into the request body verbatim.
    """
    return {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "tools": [{
            "type": "function",
            "function": {
                "name": schema_name,
                "description": "Return the located fields.",
                "parameters": schema,
            },
        }],
        "tool_choice": {"type": "function", "function": {"name": schema_name}},
        "extra_body": {
            "thinking": {"type": thinking_type,
                         "reasoning_effort": reasoning_effort},
        },
    }


def json_from_content(text: str) -> dict:
    """Parse the first balanced JSON object out of a message's content.

    A reasoning model keeps its chain-of-thought in a separate
    ``reasoning_content`` field, so ``content`` should already be the answer
    -- but it may not always arrive as a clean forced ``tool_call``. This is
    the fallback: strip any code fences, then take the first balanced
    ``{...}``. Downstream Pydantic + grounding still validate the result, so
    a loose parse here can never become bad data.
    """
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start = t.find("{")
    if start == -1:
        raise ValueError("no JSON object found in content")
    depth = 0
    for i in range(start, len(t)):
        if t[i] == "{":
            depth += 1
        elif t[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(t[start:i + 1])
    raise ValueError("unbalanced JSON object in content")


class DeepSeekClient:
    """DeepSeek (v4-pro / v4-flash) via its OpenAI-compatible endpoint.

    Structured output is obtained with a forced function call (equivalent in
    shape to the Anthropic tool path). DeepSeek does not offer OpenAI's
    grammar-level ``json_schema`` mode, so the provider guarantee is weaker
    here -- which is fine by design: Pydantic re-validation, the retry ladder,
    and grounding do the real enforcing, so the pipeline degrades gracefully
    onto a weaker provider instead of breaking.

    Thinking mode is on by default at ``reasoning_effort="high"``. Because a
    reasoning model does not always emit a clean forced ``tool_call``, the
    response parser falls back to reading a JSON object out of the message
    content. Model and effort are env-configurable so a dated snapshot can be
    pinned in deployment.
    """

    name = "deepseek"

    def __init__(self, model: str | None = None) -> None:
        from openai import OpenAI  # DeepSeek ships an OpenAI-compatible API

        # Current DeepSeek model IDs are deepseek-v4-pro / deepseek-v4-flash.
        # v4-pro supports reasoning_effort "high" and "max"; v4-flash also
        # supports "low". Kept configurable so a snapshot can be pinned and a
        # provider-side rename never becomes a code change.
        self.model = model or os.environ.get("RATECON_DEEPSEEK_MODEL",
                                             "deepseek-v4-pro")
        self.thinking_type = os.environ.get("RATECON_DEEPSEEK_THINKING",
                                            "enabled")
        self.reasoning_effort = os.environ.get(
            "RATECON_DEEPSEEK_REASONING_EFFORT", "high")
        self._client = OpenAI(
            base_url=os.environ.get("RATECON_DEEPSEEK_BASE_URL",
                                    "https://api.deepseek.com"),
            api_key=os.environ.get("DEEPSEEK_API_KEY"),
        )

    def complete_json(self, system: str, user: str, schema: dict,
                      schema_name: str) -> dict:
        resp = self._client.chat.completions.create(
            **deepseek_request_kwargs(self.model, system, user, schema,
                                      schema_name, self.thinking_type,
                                      self.reasoning_effort))
        msg = resp.choices[0].message
        calls = getattr(msg, "tool_calls", None)
        if calls:
            return json.loads(calls[0].function.arguments)
        content = getattr(msg, "content", None)
        if content:
            return json_from_content(content)
        raise RuntimeError("deepseek returned no tool_call and no JSON content")


class ReplayClient:
    """Serves recorded extractions from disk. Used by the test suite."""

    name = "replay"

    def __init__(self, directory: str | Path, key: str) -> None:
        self.path = Path(directory) / f"{key}.json"

    def complete_json(self, system: str, user: str, schema: dict,
                      schema_name: str) -> dict:
        if not self.path.exists():
            raise FileNotFoundError(f"no recorded extraction at {self.path}")
        return json.loads(self.path.read_text())


def get_client(provider: str, **kw) -> LLMClient:
    if provider == "openai":
        return OpenAIClient(**kw)
    if provider == "anthropic":
        return AnthropicClient(**kw)
    if provider == "deepseek":
        return DeepSeekClient(**kw)
    if provider == "replay":
        return ReplayClient(**kw)
    raise ValueError(f"unknown provider {provider!r}")
