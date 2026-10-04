"""call_gemini_requests：maxOutputTokens 與 finishReason / token 用量（issue #108 階段 0）。"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from translation_tool.core.lm_api_client import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    call_gemini_requests,
    extract_response_meta,
)


def _response(body: dict) -> Mock:
    r = Mock()
    r.ok = True
    r.json.return_value = body
    return r


def _body(text='{"items": []}', finish="STOP", usage=None) -> dict:
    cand = {"content": {"parts": [{"text": text}]}}
    if finish is not None:
        cand["finishReason"] = finish
    body = {"candidates": [cand]}
    if usage is not None:
        body["usageMetadata"] = usage
    return body


def _call(post, lm_cfg=None, payload=None, **kwargs):
    with patch(
        "translation_tool.core.lm_api_client.load_config",
        return_value={"lm_translator": lm_cfg or {}},
    ):
        return call_gemini_requests(
            model_name="m",
            system_prompt="s",
            payload=payload or {"items": [{"id": "0", "value": "Iron Ingot"}]},
            api_key="k",
            temperature=0.2,
            **kwargs,
        )


@pytest.fixture
def post():
    with patch("translation_tool.core.lm_api_client.requests.post") as p:
        p.return_value = _response(_body())
        yield p


def _generation_config(post) -> dict:
    return post.call_args.kwargs["json"]["generationConfig"]


# --- maxOutputTokens -------------------------------------------------------


def test_default_max_output_tokens_is_sent(post):
    _call(post)

    assert _generation_config(post)["maxOutputTokens"] == DEFAULT_MAX_OUTPUT_TOKENS
    assert DEFAULT_MAX_OUTPUT_TOKENS == 32768


def test_config_overrides_default(post):
    _call(post, {"max_output_tokens": 16000})

    assert _generation_config(post)["maxOutputTokens"] == 16000


def test_explicit_argument_beats_config(post):
    _call(post, {"max_output_tokens": 16000}, max_output_tokens=8000)

    assert _generation_config(post)["maxOutputTokens"] == 8000


@pytest.mark.parametrize("value", [0, -1])
def test_non_positive_disables_the_field(post, value):
    _call(post, {"max_output_tokens": value})

    assert "maxOutputTokens" not in _generation_config(post)


def test_invalid_config_value_falls_back_to_default(post):
    _call(post, {"max_output_tokens": "lots"})

    assert _generation_config(post)["maxOutputTokens"] == DEFAULT_MAX_OUTPUT_TOKENS


def test_existing_generation_config_fields_are_kept(post):
    _call(
        post,
        payload={
            "items": [
                {"id": "0", "value": "Iron Ingot"},
                {"id": "1", "value": "Diamond Sword"},
            ]
        },
    )

    cfg = _generation_config(post)
    assert cfg["temperature"] == 0.2
    response_format = cfg["responseFormat"]["text"]
    assert response_format["mimeType"] == "APPLICATION_JSON"
    schema = response_format["schema"]
    assert schema["type"] == "object"
    assert schema["required"] == ["items"]
    assert schema["additionalProperties"] is False
    items = schema["properties"]["items"]
    assert items["type"] == "array"
    item = items["items"]
    assert item["type"] == "object"
    assert item["required"] == ["id", "value"]
    assert item["properties"] == {
        "id": {"type": "string", "enum": ["0", "1"]},
        "value": {"type": "string"},
    }
    assert item["additionalProperties"] is False
    assert items["minItems"] == 2
    assert items["maxItems"] == 2
    assert set(cfg) >= {"temperature", "responseFormat", "maxOutputTokens"}
    assert "responseMimeType" not in cfg
    assert "responseSchema" not in cfg


def test_translation_input_payload_shape_is_unchanged(post):
    payload = {"items": [{"id": "0", "value": "Iron Ingot"}]}
    with patch(
        "translation_tool.core.lm_api_client.load_config",
        return_value={"lm_translator": {}},
    ):
        call_gemini_requests(
            model_name="m",
            system_prompt="s",
            payload=payload,
            api_key="k",
            temperature=0.2,
        )

    request_body = post.call_args.kwargs["json"]
    assert request_body["contents"][0]["parts"][0]["text"] == (
        '{"items": [{"id": "0", "value": "Iron Ingot"}]}'
    )


def test_dynamic_schema_is_fresh_for_each_batch(post):
    _call(post, payload={"items": [{"id": "a", "value": "A"}]})
    first_schema = _generation_config(post)["responseFormat"]["text"]["schema"]
    _call(
        post,
        payload={"items": [{"id": "x", "value": "X"}, {"id": "y", "value": "Y"}]},
    )
    second_schema = _generation_config(post)["responseFormat"]["text"]["schema"]

    first_items = first_schema["properties"]["items"]
    second_items = second_schema["properties"]["items"]
    assert first_items["minItems"] == first_items["maxItems"] == 1
    assert first_items["items"]["properties"]["id"]["enum"] == ["a"]
    assert second_items["minItems"] == second_items["maxItems"] == 2
    assert second_items["items"]["properties"]["id"]["enum"] == ["x", "y"]


@pytest.mark.parametrize(
    "items",
    [
        [{"id": "same", "value": "A"}, {"id": "same", "value": "B"}],
        [{"value": "missing id"}],
    ],
)
def test_invalid_batch_ids_are_rejected_before_request(post, items):
    with pytest.raises(ValueError, match="payload"):
        _call(post, payload={"items": items})

    post.assert_not_called()


def test_empty_translation_batch_is_rejected_before_request(post):
    with pytest.raises(ValueError, match="at least one item"):
        _call(post, payload={"items": []})

    post.assert_not_called()


# --- meta_out --------------------------------------------------------------


def test_meta_out_receives_finish_reason_and_usage(post):
    post.return_value = _response(
        _body(
            finish="MAX_TOKENS",
            usage={
                "promptTokenCount": 1200,
                "candidatesTokenCount": 32000,
                "thoughtsTokenCount": 700,
                "totalTokenCount": 33900,
            },
        )
    )
    meta: dict = {}

    text = _call(post, meta_out=meta)

    assert text == '{"items": []}'  # 回傳值維持純文字
    assert meta == {
        "finish_reason": "MAX_TOKENS",
        "prompt_tokens": 1200,
        "candidates_tokens": 32000,
        "thoughts_tokens": 700,
        "total_tokens": 33900,
    }


def test_meta_fields_are_none_when_response_lacks_them(post):
    post.return_value = _response(_body(finish=None, usage=None))
    meta: dict = {}

    _call(post, meta_out=meta)

    assert meta["finish_reason"] is None
    assert meta["prompt_tokens"] is None
    assert meta["thoughts_tokens"] is None


def test_meta_out_is_optional(post):
    assert _call(post) == '{"items": []}'


def test_extract_response_meta_tolerates_odd_shapes():
    assert extract_response_meta({})["finish_reason"] is None
    assert extract_response_meta({"candidates": []})["finish_reason"] is None
    assert extract_response_meta({"candidates": [None]})["finish_reason"] is None
