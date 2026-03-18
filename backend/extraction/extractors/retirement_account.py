"""
Retirement account extractors: Traditional IRA, Roth IRA, Traditional 401k, Roth 401k.

Both IRA and 401k variants share the same Claude prompt (extract_retirement.txt).
The prompt instructs Claude to auto-detect account_type from the document.

Separate extractor classes exist so the document upload route can instantiate them
for their respective classifier-returned document_type values:
  - retirement_ira  → TraditionalIRAExtractor  (handles both traditional and Roth IRA)
  - retirement_401k → Retirement401kExtractor  (handles both traditional and Roth 401k)

The distinction matters at the gate layer: IRA balance contributes to
total_pretax_retirement_balance or total_roth_balance depending on type.
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
    Path(__file__).parent.parent.parent.parent / "prompts" / "extract_retirement.txt"
)
_MODEL = "claude-sonnet-4-5"


async def _run_extraction(text: str, document_type: str) -> ExtractionResult:
    """Shared extraction logic for all retirement account types."""
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    prompt_template = _PROMPT_PATH.read_text()

    user_message = (
        f"{prompt_template}\n\n<document_text>\n{text}\n</document_text>"
    )

    response = await client.messages.create(
        model=_MODEL,
        max_tokens=4096,  # Consolidated statements with many sub-accounts need more tokens
        messages=[{"role": "user", "content": user_message}],
    )

    raw = response.content[0].text.strip()
    data = extract_json_from_response(raw)

    if data is None:
        import logging
        logging.getLogger(__name__).error(
            "Retirement extractor: Claude did not return parseable JSON. "
            "stop_reason=%s raw_preview=%r",
            response.stop_reason,
            raw[:500],
        )
        return ExtractionResult(
            document_type=document_type,
            fields={},
            extraction_notes=["Claude did not return parseable JSON."],
            overall_confidence=0.0,
        )

    # Honour Claude's account_type detection but keep document_type as the
    # classifier-assigned value so routing is stable
    result = parse_extraction_response(data, document_type=document_type)

    # If Claude detected a more specific sub-type (e.g. roth_ira vs traditional_ira),
    # preserve it in the document_type so downstream snapshot assembly can use it
    account_type_field = result.fields.get("account_type")
    if account_type_field and account_type_field.value:
        detected = str(account_type_field.value)
        # Only override to a more specific type within the same family
        if document_type == "retirement_ira" and detected in ("traditional_ira", "roth_ira"):
            result = result.model_copy(update={"document_type": detected})
        elif document_type == "retirement_401k" and detected in (
            "traditional_401k",
            "roth_401k",
        ):
            result = result.model_copy(update={"document_type": detected})

    return result


class TraditionalIRAExtractor(BaseExtractor):
    """Extracts Traditional IRA and Roth IRA statements."""

    async def extract(self, text: str) -> ExtractionResult:
        return await _run_extraction(text, document_type="retirement_ira")


class Retirement401kExtractor(BaseExtractor):
    """Extracts Traditional 401(k) and Roth 401(k) statements."""

    async def extract(self, text: str) -> ExtractionResult:
        return await _run_extraction(text, document_type="retirement_401k")


def _parse_extraction_response(data: dict, document_type: str) -> ExtractionResult:
    """Exposed for testing."""
    return parse_extraction_response(data, document_type=document_type)
