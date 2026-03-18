"""
Form 1040 extractor.

Extracts all tax-planning-relevant fields from a Form 1040, with field-level
confidence scores. Never hallucinate — returns null for fields not found.
"""

from pathlib import Path

import anthropic

from backend.config import settings
from backend.models.document import ExtractionResult
from backend.extraction.extractors.base import (
    BaseExtractor,
    parse_extraction_response,
    extract_json_from_response,
)

_PROMPT_PATH = Path(__file__).parent.parent.parent.parent / "prompts" / "extract_1040.txt"
_MODEL = "claude-sonnet-4-5"


class Form1040Extractor(BaseExtractor):
    async def extract(self, text: str) -> ExtractionResult:
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        prompt_template = _PROMPT_PATH.read_text()

        user_message = (
            f"{prompt_template}\n\n<document_text>\n{text}\n</document_text>"
        )

        response = await client.messages.create(
            model=_MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": user_message}],
        )

        raw = response.content[0].text.strip()
        data = extract_json_from_response(raw)

        if data is None:
            return ExtractionResult(
                document_type="form_1040",
                fields={},
                extraction_notes=["Claude did not return parseable JSON."],
                overall_confidence=0.0,
            )

        return _parse_extraction_response(data)


def _parse_extraction_response(data: dict) -> ExtractionResult:
    """
    Convert Claude's JSON response into a typed ExtractionResult.
    Delegates to the shared base parser; kept as a named function here
    so existing tests can import it directly from this module.
    """
    return parse_extraction_response(data, document_type="form_1040")
