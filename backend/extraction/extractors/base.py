"""
Base extractor class and shared utilities used by all document-type extractors.
"""

import json
import re
from abc import ABC, abstractmethod
from pathlib import Path

from backend.models.document import ExtractionResult, FieldConfidence


class BaseExtractor(ABC):
    """
    Contract for all extractors:
    - Receive PyMuPDF text
    - Call Claude with the type-specific prompt
    - Return ExtractionResult with field-level confidence scores
    - Never hallucinate — return null for fields not found
    """

    @abstractmethod
    async def extract(self, text: str) -> ExtractionResult:
        """
        Extract structured fields from the raw document text.

        Args:
            text: Raw text extracted via PyMuPDF from a single PDF

        Returns:
            ExtractionResult with fields dict (FieldConfidence per field),
            extraction_notes, and overall_confidence
        """
        ...


def parse_extraction_response(data: dict, document_type: str) -> ExtractionResult:
    """
    Shared parser: convert Claude's standard field-confidence JSON response into a
    typed ExtractionResult.

    Expected Claude response shape (all extractors use this schema):
    {
      "field_name": {"value": <any>, "confidence": 0.0–1.0, "inferred": bool, "note": str|null},
      ...
      "extraction_notes": ["..."],
      "overall_confidence": 0.0–1.0
    }

    Fields whose value is a list (e.g. "holdings") are stored as FieldConfidence with
    the list as the value — downstream code is responsible for interpreting them.
    """
    # Pop top-level metadata first so they don't become fields
    extraction_notes: list[str] = data.pop("extraction_notes", [])
    overall_confidence: float = float(data.pop("overall_confidence", 0.0))

    fields: dict[str, FieldConfidence] = {}
    for field_name, field_data in data.items():
        if isinstance(field_data, dict) and "value" in field_data:
            fields[field_name] = FieldConfidence(
                value=field_data.get("value"),
                confidence=float(field_data.get("confidence", 0.0)),
                inferred=bool(field_data.get("inferred", False)),
                note=field_data.get("note"),
            )

    # Derive tax_year from the statement_date or a dedicated tax_year field
    tax_year: int | None = None
    if "tax_year" in fields and fields["tax_year"].value is not None:
        tax_year = int(fields["tax_year"].value)
    elif "statement_date" in fields and fields["statement_date"].value is not None:
        # Try to parse year from ISO date string e.g. "2024-12-31"
        date_str = str(fields["statement_date"].value)
        if len(date_str) >= 4 and date_str[:4].isdigit():
            tax_year = int(date_str[:4])

    # Derive institution from the institution field if present
    institution: str | None = None
    if "institution" in fields and fields["institution"].value is not None:
        institution = str(fields["institution"].value)

    return ExtractionResult(
        document_type=document_type,
        tax_year=tax_year,
        institution=institution,
        fields=fields,
        extraction_notes=extraction_notes,
        overall_confidence=overall_confidence,
    )


def call_claude_extract(prompt_path: Path, text: str, max_tokens: int = 2048) -> dict:
    """
    Synchronous helper that builds the prompt string.
    Actual async Claude call is made by each extractor to keep them testable.
    Returns the prompt string (not the response).
    """
    raise NotImplementedError("Use the async extractor classes directly.")


def extract_json_from_response(raw_text: str) -> dict | None:
    """
    Extract the first JSON object from a Claude response string.
    Claude sometimes wraps JSON in markdown code fences.
    """
    # Strip markdown code fences if present
    stripped = re.sub(r"```(?:json)?\s*", "", raw_text).strip()

    json_match = re.search(r"\{.*\}", stripped, re.DOTALL)
    if not json_match:
        return None

    try:
        return json.loads(json_match.group())
    except json.JSONDecodeError:
        return None
