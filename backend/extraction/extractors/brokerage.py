"""
Brokerage statement extractor.

Handles Fidelity, Schwab, Vanguard, and other taxable brokerage statements.
All institutions use the same extractor — institution-specific formatting is
handled by Claude's interpretation of the prompt.

Key fields extracted:
- Account value, cash balance, securities value
- Full holdings table with per-position cost basis and holding period
- Total cost basis (null if any position has unknown basis)
- YTD dividends, interest, realized gains
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

_PROMPT_PATH = (
    Path(__file__).parent.parent.parent.parent / "prompts" / "extract_brokerage.txt"
)
_MODEL = "claude-sonnet-4-5"


class BrokerageExtractor(BaseExtractor):
    async def extract(self, text: str) -> ExtractionResult:
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        prompt_template = _PROMPT_PATH.read_text()

        user_message = (
            f"{prompt_template}\n\n<document_text>\n{text}\n</document_text>"
        )

        response = await client.messages.create(
            model=_MODEL,
            max_tokens=8192,  # Large brokerage holdings tables can exceed 4096
            messages=[{"role": "user", "content": user_message}],
        )

        raw = response.content[0].text.strip()
        data = extract_json_from_response(raw)

        if data is None:
            import logging
            logging.getLogger(__name__).error(
                "Brokerage extractor: Claude did not return parseable JSON. "
                "stop_reason=%s raw_preview=%r",
                response.stop_reason,
                raw[:500],
            )
            return ExtractionResult(
                document_type="brokerage_statement",
                fields={},
                extraction_notes=["Claude did not return parseable JSON."],
                overall_confidence=0.0,
            )

        return _parse_extraction_response(data)


def _parse_extraction_response(data: dict) -> ExtractionResult:
    return parse_extraction_response(data, document_type="brokerage_statement")
