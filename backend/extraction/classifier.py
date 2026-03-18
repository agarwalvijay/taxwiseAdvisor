"""
Document classifier.

Accepts PyMuPDF-extracted text, calls Claude with prompts/classify.txt,
and returns a ClassificationResult. Rejects documents with confidence < 0.90
or unknown document_type.
"""

import json
import re
from pathlib import Path

import anthropic

from backend.config import settings
from backend.models.document import ClassificationResult

_PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "classify.txt"
_MODEL = "claude-sonnet-4-5"


def _load_prompt() -> str:
    return _PROMPT_PATH.read_text()


async def classify_document(text: str) -> ClassificationResult:
    """
    Classify a document from its extracted text.

    Returns ClassificationResult. If confidence < 0.90 or document_type == 'unknown',
    rejection_reason will be set and should be treated as a rejection.
    """
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    prompt_template = _load_prompt()

    # Truncate text to avoid token limits — classification only needs the first ~3000 chars
    truncated_text = text[:3000]

    user_message = f"{prompt_template}\n\n<document_text>\n{truncated_text}\n</document_text>"

    response = await client.messages.create(
        model=_MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = response.content[0].text.strip()

    # Extract JSON from response (Claude may wrap it in markdown code fences)
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not json_match:
        return ClassificationResult(
            document_type="unknown",
            confidence=0.0,
            rejection_reason="Claude did not return parseable JSON for classification.",
        )

    data = json.loads(json_match.group())
    result = ClassificationResult(**data)

    # Enforce rejection rules
    if result.confidence < settings.confidence_threshold_classification:
        if not result.rejection_reason:
            result.rejection_reason = (
                f"Classification confidence {result.confidence:.2f} is below the "
                f"required threshold of {settings.confidence_threshold_classification:.2f}. "
                "The document could not be reliably identified."
            )

    if result.document_type == "unknown":
        if not result.rejection_reason:
            result.rejection_reason = (
                "Document type could not be determined. "
                "Supported types: Form 1040, brokerage statement, 401k statement, "
                "IRA statement, W-2, or SSA benefit estimate."
            )

    return result
