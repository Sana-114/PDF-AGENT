"""Decode structured output without silently choosing between contradictory fields."""

import json


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON contains a repeated field; no value was selected.")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("Non-finite JSON constants are not structured evidence.")


def strict_json(text):
    """Reject duplicate keys and NaN/Infinity at every depth; never repair text."""
    return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)


def response_diagnostics(response):
    """Retain received text for diagnosis only, even when it cannot be published."""
    result = {key: response.get(key) for key in ("incomplete_details", "usage")}
    result["response_status"] = response.get("status")
    texts = []
    output = response.get("output")
    for item in output if isinstance(output, list) else []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        parts = [
            part["text"] for part in content if isinstance(part, dict)
            and part.get("type") == "output_text" and isinstance(part.get("text"), str)
        ] if isinstance(content, list) else []
        if parts:
            texts.append("".join(parts))
    result["message_texts"] = texts
    # Multiple messages remain visibly distinct, never a guessed combined answer.
    result["unparsed_text"] = texts[0] if len(texts) == 1 else None
    return result
