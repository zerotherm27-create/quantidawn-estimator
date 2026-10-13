import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Claude Sonnet - matches the model already proven in the live estimator demo.
# Kept as a single named constant so it is changed in exactly one place if
# Anthropic ships a newer model later.
CLAUDE_MODEL = "claude-sonnet-4-6"

# Safety cap on how much raw document text is sent per call. Claude's context
# window comfortably fits a full spec document, so this is a generous ceiling
# to control cost and latency, not an artificial "demo" limit.
MAX_INPUT_CHARS = 60000

REGIONAL_NORMS = {
    "US": {
        "units": "Imperial (ft, in, sq ft)",
        "currency": "USD",
        "standard": "CSI MasterFormat",
        "system_prompt": (
            "You are an expert US Construction Estimator. Use imperial units, "
            "CSI MasterFormat divisions, and US terminology (e.g., drywall, lumber studs)."
        ),
    },
    "AU": {
        "units": "Metric (m, mm, m2)",
        "currency": "AUD",
        "standard": "Australian Standard Method of Measurement (ASMM)",
        "system_prompt": (
            "You are an expert Australian Quantity Surveyor. Use metric units "
            "(meters, millimeters), ASMM standards, and Australian trade terms "
            "(e.g., plasterboard, timber framing)."
        ),
    },
    "SG": {
        "units": "Metric (m, mm, m2)",
        "currency": "SGD",
        "standard": "BCA Standard Method of Measurement (SMM)",
        "system_prompt": (
            "You are an expert Singaporean Quantity Surveyor. Use metric units, "
            "BCA regulations, PSSCOC frameworks, and local grading terms "
            "(e.g., Grade 40 concrete, rebar configurations)."
        ),
    },
}
