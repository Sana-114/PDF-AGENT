import asyncio

import pytest

from app.llm.base import GeneratedSegmentTranslations, LLMResponseError
from app.services.academic_translation import translate_protected_segments


def test_different_blocks_have_distinct_tokens_and_roundtrip_their_own_values():
    class Echo:
        async def translate_segments(self, segments, *args):
            self.segments = segments
            return GeneratedSegmentTranslations(dict(segments))

    provider = Echo()
    result = asyncio.run(translate_protected_segments(provider,
        [("one", "The score is 13."), ("two", "The score is 29.")], "en", "zh"))
    assert result.items == {"one": "The score is 13.", "two": "The score is 29."}
    assert provider.segments[0][1] != provider.segments[1][1]


def test_cross_block_token_swap_is_rejected_and_raw_failed_translation_retained():
    class Swap:
        async def translate_segments(self, segments, *args):
            return GeneratedSegmentTranslations({segments[0][0]: segments[1][1],
                                                  segments[1][0]: segments[0][1]})
    with pytest.raises(LLMResponseError) as failure:
        asyncio.run(translate_protected_segments(Swap(),
            [("one", "The score is 13."), ("two", "The score is 29.")], "en", "zh"))
    assert failure.value.raw_translation_output["block_id"] == "one"
    diagnostic = failure.value.raw_translation_output
    assert diagnostic["returned_translation"] != diagnostic["protected_source"]
