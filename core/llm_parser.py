import json

import anthropic
import pdfplumber

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, MAX_INPUT_CHARS, REGIONAL_NORMS


class LLMParserError(Exception):
    """Raised when the document cannot be read or the model response cannot
    be used. Callers (app.py) should catch this and show the user a clear
    message instead of letting the app crash."""


class LLMParser:
    def __init__(self):
        if not ANTHROPIC_API_KEY:
            raise LLMParserError(
                "ANTHROPIC_API_KEY is not set. Add it to your .env file before running the app."
            )
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    def extract_text_from_pdf(self, pdf_path: str) -> str:
        """Extracts all text from a PDF. Raises LLMParserError on a corrupt
        or unreadable file instead of letting the exception surface raw."""
        text_parts = []
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    extracted = page.extract_text()
                    if extracted:
                        text_parts.append(extracted)
        except Exception as exc:
            raise LLMParserError(f"Could not read this PDF: {exc}") from exc

        text = "\n".join(text_parts)
        if not text.strip():
            raise LLMParserError(
                "No extractable text found in this PDF. It may be a scanned "
                "image with no text layer - try running it through OCR first."
            )
        return text

    def parse_scope_and_materials(self, raw_text: str, region: str) -> dict:
        """Sends document text to Claude and returns a structured dict:

        {
            "scope_summary": str,
            "materials": [
                {"description": str, "category": str, "quantity": float | None,
                 "unit": str, "notes": str},
                ...
            ],
            "flags": [str, ...]
        }

        This is what actually gets written into the Excel workbook - unlike
        the previous version, nothing here is discarded after being shown
        in the UI.
        """
        norm = REGIONAL_NORMS.get(region, REGIONAL_NORMS["US"])
        trimmed_text = raw_text[:MAX_INPUT_CHARS]

        system_prompt = (
            f"{norm['system_prompt']} Respond with ONLY valid JSON, no markdown "
            "fences, no prose outside the JSON, in exactly this shape: "
            '{"scope_summary": "2-3 sentence summary of the project scope", '
            '"materials": [{"description": "item name", "category": '
            '"trade or division", "quantity": number or null if not '
            'determinable from the text, "unit": "unit of measure", "notes": '
            '"anything the estimator should double-check"}], "flags": '
            '["any ambiguity, missing information, or assumption the estimator '
            'must verify"]}. '
            f"Use {norm['standard']} conventions and {norm['units']}. "
            f"Report currency-relevant items in {norm['currency']} where a "
            "rate is mentioned in the source text, otherwise leave rate out "
            "entirely - this tool only extracts scope and quantities, not pricing."
        )

        user_prompt = (
            "Extract the explicit scope of work and structural materials "
            "from this construction document text. Only include items that "
            "are actually stated or clearly implied in the text - do not "
            "invent quantities.\n\n"
            f"Raw text:\n{trimmed_text}"
        )

        try:
            response = self.client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=2000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except anthropic.APIError as exc:
            raise LLMParserError(f"The AI request failed: {exc}") from exc

        text_blocks = [block.text for block in response.content if block.type == "text"]
        raw_output = "".join(text_blocks).strip()
        cleaned = raw_output.replace("```json", "").replace("```", "").strip()

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise LLMParserError(
                "The AI response was not valid JSON, so nothing could be "
                f"loaded into the workbook. Raw response started with: "
                f"{cleaned[:200]!r}"
            ) from exc

        parsed.setdefault("scope_summary", "")
        parsed.setdefault("materials", [])
        parsed.setdefault("flags", [])
        return parsed
