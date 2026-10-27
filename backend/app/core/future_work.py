"""Shared source-role signals; plans are not completed measurements or releases."""

import re

FUTURE_SIGNAL = re.compile(
    r"(?:future\s+(?:work|research)|in\s+(?:the\s+)?future\b|remain(?:s|ing)?\s+(?:to|an?\s+open)|"
    r"further\s+(?:work|research)|further\s+study\s+(?:is|will be)\s+(?:needed|required)|"
    r"open\s+(?:question|problem)|we\s+(?:plan|hope)|"
    r"未来(?:工作|研究|方向)|后续(?:工作|研究)|尚待|有待|仍需)",
    re.IGNORECASE,
)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?。！？])\s+")
FUTURE_TITLE = re.compile(
    r"(?:\d+[.\s]*)?(?:(?:limitations?|conclusions?|discussion)\s*(?:&|and)\s*)?"
    r"future\s+(?:work|research|directions?)\s*[.:：]?|(?:未来工作|研究展望|总结与展望)\s*[：:]?",
    re.I,
)


def is_future_plan(text):
    """Retain the existing extraction policy, now shared without a service cycle."""
    if FUTURE_TITLE.fullmatch(text.strip()) or not FUTURE_SIGNAL.search(text):
        return False
    release = re.search(r"\bwe\s+(?:release|provide|open[- ]source)\b", text, re.I)
    explicit_plan = re.search(
        r"\bwe\s+(?:plan|will)\b|\b(?:should|remain\w*)\b|leave.{0,80}future", text, re.I,
    )
    return not release or bool(explicit_plan)


def future_goal_quotes(text):
    """Literal plan sentences, excluding nearby definitions and completed results.

    These are conservative source-role hints, not scientific outcome judgments.
    Implicit plans without a recognizable signal remain a manual-review boundary.
    """
    goals = []
    for sentence in SENTENCE_SPLIT.split(text):
        sentence = sentence.strip()
        if not sentence or not (
            is_future_plan(sentence)
            or re.search(r"\bresearch\s+goals?\b|另一.{0,12}研究目标", sentence, re.I)
        ):
            continue
        # A current-study contrast is context, not part of the future target.
        # Keep a literal suffix and retain BOTH clauses when both are plans.
        contrast = re.search(r"\bbut\s+|\bhowever,?\s+|但是|然而", sentence, re.I)
        if contrast:
            prefix, suffix = sentence[:contrast.start()], sentence[contrast.end():]
            if is_future_plan(suffix) and not is_future_plan(prefix):
                sentence = suffix.strip()
        goals.append(sentence)
    return list(dict.fromkeys(goals))
