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


class DeepSeekClient:
    """DeepSeek via its OpenAI-compatible endpoint, using function calling.

    DeepSeek does not support OpenAI's ``json_schema`` structured-output mode
    for the final message -- only a looser ``json_object`` JSON mode, plus a
    beta ``strict`` function-calling mode with a narrower schema vocabulary
    (no format/pattern/length/range constraints). So the grammar-level
    guarantee the OpenAI path leans on is not available here.

    That is fine for this pipeline, and the reason is the whole design: the
    schema is re-validated client-side with Pydantic, the retry ladder
    repairs malformed output, and grounding discards anything the source did
    not contain. The provider's guarantee is a convenience, not the thing
    keeping bad data out. This client therefore uses forced function calling
    (equivalent in shape to the Anthropic tool path) and lets the downstream
    validation do the enforcing.
    """

    name = "deepseek"

    def __init__(self, model: str | None = None) -> None:
        from openai import OpenAI  # DeepSeek ships an OpenAI-compatible API

        # deepseek-chat/-reasoner are legacy aliases retired mid-2026; current
        # IDs are deepseek-v4-flash / deepseek-v4-pro. Kept configurable so a
        # provider-side rename never becomes a code change, and so a dated
        # snapshot can be pinned in deployment to keep behaviour stable.
        self.model = model or os.environ.get("RATECON_DEEPSEEK_MODEL",
                                             "deepseek-chat")
        self._client = OpenAI(
            base_url=os.environ.get("RATECON_DEEPSEEK_BASE_URL",
                                    "https://api.deepseek.com"),
            api_key=os.environ.get("DEEPSEEK_API_KEY"),
        )

    def complete_json(self, system: str, user: str, schema: dict,
                      schema_name: str) -> dict:
        resp = self._client.chat.completions.create(
            model=self.model,
            temperature=0,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            tools=[{
                "type": "function",
                "function": {
                    "name": schema_name,
                    "description": "Return the located fields.",
                    "parameters": schema,
                },
            }],
            tool_choice={"type": "function", "function": {"name": schema_name}},
        )
        call = resp.choices[0].message.tool_calls
        if not call:
            raise RuntimeError("model returned no tool_call")
        return json.loads(call[0].function.arguments)


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
