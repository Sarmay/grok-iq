from __future__ import annotations

from app.reasoning_policy import (
    canonical_reasoning_model,
    default_reasoning_model_policies,
    merge_missing_default_reasoning_policies,
    normalize_reasoning_model_policies,
    resolve_reasoning_model_policy,
)


def test_grok_4_7_requires_reasoning_and_observes_message_images() -> None:
    policies = normalize_reasoning_model_policies(None)

    chat = resolve_reasoning_model_policy(
        policies,
        model_upstream_model="grok-4.7",
        operation="chat",
    )
    messages = resolve_reasoning_model_policy(
        policies,
        model_upstream_model="Build/grok-4.7",
        operation="messages",
    )
    with_images = resolve_reasoning_model_policy(
        policies,
        model_upstream_model="Build/grok-4.7",
        operation="messages",
        media_input_images=2,
    )

    assert chat.mode == "required"
    assert messages.mode == "required"
    assert with_images.mode == "observe"
    assert with_images.media_input_mode == "observe"


def test_merge_inserts_grok_4_7_before_composer_and_keeps_custom_rows() -> None:
    stored = [
        item
        for item in default_reasoning_model_policies()
        if canonical_reasoning_model(item["model"]) != "grok-4.7"
    ]
    stored[0] = {**stored[0], "minCount": 9}

    merged = merge_missing_default_reasoning_policies(
        stored,
        models=("grok-4.7",),
    )

    operations = [
        (item["model"], item["operation"])
        for item in merged
        if canonical_reasoning_model(str(item["model"])) == "grok-4.7"
    ]
    composer = next(
        index
        for index, item in enumerate(merged)
        if item["model"] == "Build/grok-composer-2.5-fast"
    )
    assert operations == [
        ("Build/grok-4.7", "chat"),
        ("Build/grok-4.7", "responses"),
        ("Build/grok-4.7", "messages"),
    ]
    assert merged[composer - 1]["operation"] == "messages"
    assert merged[0]["minCount"] == 9
    assert (
        merge_missing_default_reasoning_policies(merged, models=("grok-4.7",))
        is merged
    )


def test_merge_keeps_a_custom_grok_4_7_rule_and_fills_the_missing_operations() -> None:
    stored = [
        item
        for item in default_reasoning_model_policies()
        if canonical_reasoning_model(item["model"]) != "grok-4.7"
    ]
    stored.append(
        {
            "model": "grok-4.7",
            "operation": "chat",
            "mode": "observe",
            "minimumOutputTokens": 64,
            "minCount": 3,
            "mediaInputMode": "inherit",
        }
    )

    merged = merge_missing_default_reasoning_policies(
        stored,
        models=("Build/grok-4.7",),
    )

    keys = [(item["model"], item["operation"]) for item in merged]
    chat_index = keys.index(("grok-4.7", "chat"))
    assert keys[chat_index + 1] == ("Build/grok-4.7", "responses")
    assert keys[chat_index + 2] == ("Build/grok-4.7", "messages")
    assert merged[chat_index]["mode"] == "observe"
    assert merged[chat_index]["minCount"] == 3
