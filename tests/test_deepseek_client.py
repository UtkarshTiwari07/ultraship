"""DeepSeek request-shape and content-fallback tests.

These exercise the pure helpers in ``ratecon.llm`` so the DeepSeek wiring has
a regression guard without needing the ``openai`` SDK or a live API key --
the same offline-by-default property the rest of the suite has.
"""

import json

import pytest

from ratecon.llm import deepseek_request_kwargs, json_from_content

SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}}}


def test_request_targets_v4_pro_on_high():
    kw = deepseek_request_kwargs(
        "deepseek-v4-pro", "SYS", "USR", SCHEMA, "rate_confirmation",
        "enabled", "high")
    assert kw["model"] == "deepseek-v4-pro"
    # "v4 pro on high" -> thinking enabled at reasoning_effort high, in extra_body
    assert kw["extra_body"]["thinking"] == {
        "type": "enabled", "reasoning_effort": "high"}
    # JSON mode, NOT a forced tool_choice (thinking mode rejects that)
    assert kw["response_format"] == {"type": "json_object"}
    assert "tool_choice" not in kw
    assert "tools" not in kw
    assert kw["temperature"] == 0
    assert kw["messages"][0] == {"role": "system", "content": "SYS"}
    # json_object requires the word "json" in the prompt, and we show the schema
    user_msg = kw["messages"][1]["content"]
    assert user_msg.startswith("USR")
    assert "json" in user_msg.lower()
    assert "properties" in user_msg  # the schema was embedded


def test_reasoning_effort_is_passed_through():
    kw = deepseek_request_kwargs(
        "deepseek-v4-pro", "s", "u", SCHEMA, "n", "enabled", "max")
    assert kw["extra_body"]["thinking"]["reasoning_effort"] == "max"


def test_json_from_content_plain():
    assert json_from_content('{"a": 1}') == {"a": 1}


def test_json_from_content_fenced():
    assert json_from_content('```json\n{"a": 1}\n```') == {"a": 1}


def test_json_from_content_ignores_surrounding_text():
    got = json_from_content('Here is the result:\n{"a": {"b": 2}}\ndone.')
    assert got == {"a": {"b": 2}}


def test_json_from_content_takes_first_balanced_object():
    assert json_from_content('{"a": 1} {"b": 2}') == {"a": 1}


def test_json_from_content_raises_without_object():
    with pytest.raises(ValueError):
        json_from_content("no json here")
