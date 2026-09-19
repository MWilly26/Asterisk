"""Model IDs, endpoints, thresholds, and paths. Edit here, never inline."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# --- Provider selection ------------------------------------------------------
# "anthropic" during development, "nvidia" for the final Nemotron build.
PROVIDER: str = os.environ.get("CLAUSE_PROVIDER", "anthropic").lower()

# --- Anthropic ---------------------------------------------------------------
ANTHROPIC_MODEL = "claude-opus-5"

# --- NVIDIA ------------------------------------------------------------------
# Verify against build.nvidia.com before the final swap (PLAN §3.3).
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_NANO_MODEL = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
NVIDIA_PARSE_MODEL = "nvidia/nemotron-parse"


def default_model() -> str:
    return NVIDIA_NANO_MODEL if PROVIDER == "nvidia" else ANTHROPIC_MODEL


# --- Pricing (USD per 1M tokens: input, output) for the §6.4 cost table ------
# Anthropic first-party rates as of 2026-09. Fill in the NVIDIA row from the
# provider's price sheet before the final run; None renders as "n/a".
PRICING: dict[str, tuple[float, float] | None] = {
    ANTHROPIC_MODEL: (5.00, 25.00),
    NVIDIA_NANO_MODEL: None,
    NVIDIA_PARSE_MODEL: None,
}

# --- Client behaviour --------------------------------------------------------
MAX_ATTEMPTS = 3            # retry on 429 / 5xx, then raise
BACKOFF_BASE_S = 1.0        # 1s, 2s, 4s
REQUEST_TIMEOUT_S = 120.0
CACHE_DIR = REPO_ROOT / ".cache"
CALL_LOG = REPO_ROOT / "runs" / "calls.jsonl"

# --- Pipeline thresholds -----------------------------------------------------
CLASSIFY_BATCH_SIZE = 8
CLASSIFY_MAX_TOKENS = 1536 # observed Nano batches use <=584; lower reservation avoids hosted worker exhaustion
CLASSIFY_WORKERS = 4        # concurrent classify batches in the pipeline (retry absorbs 429s)
NVIDIA_CLASSIFY_BATCH_SIZE = 16 # fewer hosted requests; output remains within CLASSIFY_MAX_TOKENS
NVIDIA_CLASSIFY_WORKERS = 1 # hosted reasoning NIM rejects concurrent batches with ResourceExhausted
LOW_CONFIDENCE = 0.5        # below this the UI shows "verify this"
UNVERIFIED_CONFIDENCE = 0.3 # extracted value not found in source text

# --- Clause categories (classify output vocabulary; compute maps these to $) --
# Keep in sync with prompts/classify.txt.
CATEGORIES: dict[str, str] = {
    "balloon": "a large final payment beyond the regular installments",
    "origination_fee": "an origination, processing, or funding fee, especially one excluded from the stated rate or financed into the balance",
    "prepayment_penalty": "a fee or premium for paying off early",
    "late_fee": "late charges, especially percentage-based, compounding, or rate escalation after a late payment",
    "auto_renewal": "automatic renewal for another term unless cancelled in a narrow window",
    "cross_default": "default under any other obligation triggers default here",
    "personal_guarantee": "confession of judgment, personal guarantee, or waiver of notice/hearing before judgment",
    "insurance": "mandatory insurance purchased through the lender at a cost the lender sets",
    "venue": "distant jurisdiction, mandatory arbitration, jury waiver, or class-action waiver",
    "blanket_lien": "security interest in all business assets rather than just the equipment",
    "payment_terms": "principal, rate, term, or installment amount",
    "standard": "boilerplate with no unusual financial consequence",
}
RISK_LEVELS = ["high", "medium", "low", "standard"]
