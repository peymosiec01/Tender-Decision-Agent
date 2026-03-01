import json
import re
import openai
from foundry_local import FoundryLocalManager
from dataclasses import dataclass
from typing import List

from prompt import ANALYST_SYSTEM_PROMPT, FORMATTER_SYSTEM_PROMPT

# ─────────────────────────────────────────────────────────────
# 1. MODEL SETUP
#
# phi-4-mini handles BOTH stages:
#   Stage 1 — structured analyst findings (no chain-of-thought needed)
#   Stage 2 — JSON extraction
#
# ─────────────────────────────────────────────────────────────

ANALYST_MODEL   = "phi-4-mini"
FORMATTER_MODEL = "phi-4-mini"

analyst_manager   = FoundryLocalManager(ANALYST_MODEL)
formatter_manager = analyst_manager   # same model — reuse the same manager

analyst_client = openai.OpenAI(
    base_url=analyst_manager.endpoint,
    api_key=analyst_manager.api_key,
)
formatter_client = analyst_client     # same endpoint

ANALYST_MODEL_ID   = analyst_manager.get_model_info(ANALYST_MODEL).id
FORMATTER_MODEL_ID = ANALYST_MODEL_ID


# ─────────────────────────────────────────────────────────────
# 2. DATA MODELS
# ─────────────────────────────────────────────────────────────

@dataclass
class TenderDocument:
    tender_id: str
    budget: float
    delivery_months: int
    evaluation_criteria: str
    min_annual_turnover: float
    required_certifications: List[str]
    min_years_experience: int
    min_public_liability: float
    min_past_contract_value: float
    min_employees: int


@dataclass
class Bid:
    bid_id: str
    company_name: str
    bid_price: float
    proposed_delivery_months: int
    annual_turnover: float
    certifications: List[str]
    years_experience: int
    public_liability_cover: float
    largest_past_contract: float
    employee_count: int
    technical_approach: str
    added_value: str
    notes: str


@dataclass
class BidEvaluation:
    bid: Bid
    qualification_passed: bool
    scores: dict
    total_score: int
    recommendation_score: int
    confidence: str
    recommendation: str
    recommendation_reason: str
    summary: str



# ─────────────────────────────────────────────────────────────
# 3. CONTEXT BUILDER
# ─────────────────────────────────────────────────────────────

def build_context(tender: TenderDocument, bid: Bid) -> str:
    return f"""
=== TENDER DOCUMENT ===
Tender ID: {tender.tender_id}
Budget: £{tender.budget:,.0f}
Delivery Required: {tender.delivery_months} months
Evaluation Priority: {tender.evaluation_criteria}

Min Turnover: £{tender.min_annual_turnover:,.0f}
Required Certs: {', '.join(tender.required_certifications) or 'None'}
Min Experience: {tender.min_years_experience} years
Min Public Liability: £{tender.min_public_liability:,.0f}
Min Past Contract: £{tender.min_past_contract_value:,.0f}
Min Employees: {tender.min_employees}

=== BID ===
Company: {bid.company_name}
Bid Price: £{bid.bid_price:,.0f}
Delivery: {bid.proposed_delivery_months} months
Turnover: £{bid.annual_turnover:,.0f}
Certifications: {', '.join(bid.certifications) or 'None'}
Experience: {bid.years_experience} years
Public Liability: £{bid.public_liability_cover:,.0f}
Largest Contract: £{bid.largest_past_contract:,.0f}
Employees: {bid.employee_count}
Technical: {bid.technical_approach}
Added Value: {bid.added_value}
Notes: {bid.notes}
"""


# ─────────────────────────────────────────────────────────────
# 4. JSON REPAIR HELPER (thin safety net for phi-4-mini edge cases)
# ─────────────────────────────────────────────────────────────

def _sanitize_json_string(raw: str) -> str:
    """Light sanitisation for the small number of JSON mistakes phi-4-mini still makes."""

    # Strip non-printable control chars (keep tab \x09, newline \x0a, CR \x0d)
    raw = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', raw)

    # Stray quote immediately after a bare value:  null"  →  null
    raw = re.sub(r'(null|true|false|\d)"', r'\1', raw)

    # Trailing commas before closing brace/bracket
    raw = re.sub(r',\s*([}\]])', r'\1', raw)

    # Incomplete step objects missing finding/conclusion (formatter truncated mid-array)
    raw = re.sub(
        r'\{\s*"label"\s*:\s*"([^"]+)"\s*\}',
        r'{"label": "\1", "finding": null, "conclusion": null}',
        raw
    )

    return raw


def _safe_parse_json(raw: str) -> dict:
    """Parse JSON, sanitising and structurally repairing as needed."""
    start = raw.find("{")
    end   = raw.rfind("}") + 1
    if start == -1:
        raise ValueError("No JSON object found in formatter output.")

    candidate = raw[start:end]

    # Pass 1 — fast path (clean output needs no work)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # Pass 2 — sanitise all known bad patterns then retry
    fixed = _sanitize_json_string(candidate)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # Pass 3 — sanitise + close any unclosed structures
    repaired = re.sub(r",\s*$", "", fixed.rstrip())
    repaired += "]" * max(repaired.count("[") - repaired.count("]"), 0)
    repaired += "}" * max(repaired.count("{") - repaired.count("}"), 0)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Could not parse or repair formatter JSON.\nRaw output:\n{raw}\nError: {e}"
        )


# ─────────────────────────────────────────────────────────────
# 5. CLI DISPLAY HELPERS
# ─────────────────────────────────────────────────────────────

# ANSI colours — gracefully degrade on terminals that don't support them
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
CYAN   = "\033[36m"
YELLOW = "\033[33m"
GREEN  = "\033[32m"
RED    = "\033[31m"
BLUE   = "\033[34m"
WHITE  = "\033[37m"


def _divider(char: str = "─", width: int = 70, colour: str = DIM) -> None:
    print(f"{colour}{char * width}{RESET}")


def _header(text: str) -> None:
    _divider("═")
    print(f"{BOLD}{CYAN}  {text}{RESET}")
    _divider("═")


def _step_header(index: int, label: str) -> None:
    print(f"\n{BOLD}{BLUE}  Step {index}: {label}{RESET}")
    _divider()


def _field(label: str, value: str, colour: str = WHITE) -> None:
    print(f"  {DIM}{label:<14}{RESET} {colour}{value}{RESET}")


def _score_bar(score: int, max_score: int = 20, width: int = 20) -> str:
    filled = round((score / max_score) * width)
    bar    = "█" * filled + "░" * (width - filled)
    colour = GREEN if score >= 14 else YELLOW if score >= 8 else RED
    return f"{colour}{bar}{RESET} {BOLD}{score}/{max_score}{RESET}"


def _verdict_colour(recommendation: str) -> str:
    return {
        "AWARD":     GREEN,
        "SHORTLIST": YELLOW,
        "REJECT":    RED,
    }.get(recommendation, WHITE)


def _print_analyst_output(analysis_text: str) -> None:
    """Print the structured analyst findings section."""
    _step_header(1, f"Analyst Evaluation  ({ANALYST_MODEL_ID})")
    for line in analysis_text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("QUALIFICATION:"):
            colour = GREEN if "PASS" in line else RED
            print(f"  {BOLD}{colour}{line}{RESET}")
        elif line.endswith("FINDING:") or "FINDING:" in line:
            print(f"\n  {BOLD}{YELLOW}{line}{RESET}")
        elif "SCORE:" in line or line.startswith("RECOMMENDATION SCORE:"):
            print(f"  {CYAN}{line}{RESET}")
        elif line.startswith("CONFIDENCE:"):
            print(f"  {BLUE}{line}{RESET}")
        elif line.startswith("SUMMARY:"):
            print(f"\n  {BOLD}SUMMARY:{RESET}")
        else:
            print(f"  {line}")


def _print_formatter_step() -> None:
    _step_header(2, f"JSON Extraction  ({FORMATTER_MODEL_ID})")
    print(f"  {DIM}Parsing analyst output into structured JSON...{RESET}")


def _print_evaluation_steps(steps: list) -> None:
    """Print each extracted evaluation step with finding and conclusion."""
    _step_header(3, "Extracted Evaluation Steps")
    for i, step in enumerate(steps, 1):
        label      = step.get("label", "Unknown")
        finding    = step.get("finding") or "—"
        conclusion = step.get("conclusion") or "—"
        print(f"\n  {BOLD}[{i}] {label}{RESET}")
        print(f"      {DIM}Finding:   {RESET}{finding}")
        print(f"      {DIM}Conclusion:{RESET} {conclusion}")


def _print_scores_table(scores: dict, total: int, rec_score: int, confidence: str) -> None:
    """Print a formatted scores table with bar charts."""
    _step_header(4, "Scoring Summary")
    labels = {
        "price":       "Price",
        "delivery":    "Delivery",
        "technical":   "Technical",
        "compliance":  "Compliance",
        "added_value": "Added Value",
    }
    for key, label in labels.items():
        score = scores.get(key, 0)
        bar   = _score_bar(score)
        print(f"  {label:<14} {bar}")

    _divider(width=50)
    total_colour = GREEN if total >= 70 else YELLOW if total >= 50 else RED
    print(f"  {'TOTAL':<14} {BOLD}{total_colour}{total}/100{RESET}")
    print(f"  {'REC. SCORE':<14} {BOLD}{rec_score}/100{RESET}  {DIM}Confidence: {confidence}{RESET}")


def _print_verdict(recommendation: str, reason: str, summary: str,
                   qual_passed: bool) -> None:
    """Print the final agent verdict."""
    _step_header(5, "Final Verdict")
    colour = _verdict_colour(recommendation)
    qual_label = f"{GREEN}PASS{RESET}" if qual_passed else f"{RED}FAIL{RESET}"

    print(f"  {DIM}Qualification: {RESET}{qual_label}")
    print(f"\n  {BOLD}Recommendation:  {colour}{recommendation}{RESET}")
    if reason:
        print(f"\n  {DIM}Reason:{RESET} {reason}")
    if summary:
        print(f"\n  {DIM}Summary:{RESET}")
        # Wrap summary at 90 chars
        words, line = summary.split(), ""
        for word in words:
            if len(line) + len(word) + 1 > 90:
                print(f"    {line}")
                line = word
            else:
                line = f"{line} {word}".strip()
        if line:
            print(f"    {line}")
    _divider("═")
    print()


# ─────────────────────────────────────────────────────────────
# 6. EVALUATION ENGINE
# ─────────────────────────────────────────────────────────────

def evaluate_bid(tender: TenderDocument, bid: Bid) -> BidEvaluation:

    context = build_context(tender, bid)

    # ── Banner ────────────────────────────────────────────────
    _header(f"Evaluating Bid: {bid.company_name}  [{bid.bid_id}]")
    print(f"  {DIM}Tender:{RESET} {tender.tender_id}   "
          f"{DIM}Budget:{RESET} £{tender.budget:,.0f}   "
          f"{DIM}Delivery:{RESET} {tender.delivery_months} months\n")

    # ── Stage 1: Analyst evaluation (streaming) ──────────────
    print(f"  {DIM}▶ Calling analyst model ({ANALYST_MODEL_ID})...{RESET}")

    stream = analyst_client.chat.completions.create(
        model=ANALYST_MODEL_ID,
        messages=[
            {"role": "system", "content": ANALYST_SYSTEM_PROMPT},
            {"role": "user",   "content": context},
        ],
        temperature=0.1,
        max_tokens=600,    # findings are short — 5 one-line findings + scores + summary
        stream=True,
    )

    # Stream and collect response live
    full_response = ""
    print(f"\n  {DIM}{'─' * 66}{RESET}")
    print(f"  {DIM}Streaming analyst output...{RESET}\n")
    for chunk in stream:
        delta = chunk.choices[0].delta.content or ""
        full_response += delta
        print(delta, end="", flush=True)
    print(f"\n  {DIM}{'─' * 66}{RESET}\n")

    analysis_text = full_response.strip()

    # ── Sanitise £ before passing to formatter ────────────────
    analysis_text_for_formatter = analysis_text.replace("£", "GBP ")

    # ── Display structured analyst output ────────────────────
    _print_analyst_output(analysis_text)

    # ── Stage 2: JSON formatter ───────────────────────────────
    _print_formatter_step()
    MAX_RETRIES = 2
    data = {}

    for attempt in range(1, MAX_RETRIES + 1):
        if attempt > 1:
            print(f"  {YELLOW}↻ Retry attempt {attempt}/{MAX_RETRIES}...{RESET}")

        formatter_response = formatter_client.chat.completions.create(
            model=FORMATTER_MODEL_ID,
            messages=[
                {"role": "system", "content": FORMATTER_SYSTEM_PROMPT},
                {"role": "user",   "content": analysis_text_for_formatter},
            ],
            temperature=0,
            max_tokens=1800,
            response_format={"type": "json_object"},
        )

        raw_json = formatter_response.choices[0].message.content.strip()

        try:
            data = _safe_parse_json(raw_json)
            print(f"  {GREEN}✔ JSON extracted successfully.{RESET}")
            break
        except ValueError as e:
            print(f"  {RED}✘ Parse attempt {attempt}/{MAX_RETRIES} failed: {e}{RESET}")
            if attempt == MAX_RETRIES:
                print(f"  {RED}✘ All formatter attempts failed. Using empty fallback.{RESET}")
                data = {}

    # ── Stage 3: Display extracted steps ─────────────────────
    steps = data.get("steps", [])
    if steps:
        _print_evaluation_steps(steps)

    # ── Stage 4: Compute final scores and verdict ─────────────
    scores = {
        k: int(data.get("scores", {}).get(k) or 0)
        for k in ["price", "delivery", "technical", "compliance", "added_value"]
    }

    total_score = sum(scores.values())
    rec_score   = int(data.get("recommendation_score") or 0)
    qual_passed = bool(data.get("qualification_passed") or False)
    confidence  = data.get("confidence") or "Low"

    _print_scores_table(scores, total_score, rec_score, confidence)

    if not qual_passed:
        recommendation = "REJECT"
    elif rec_score >= 75:
        recommendation = "AWARD"
    elif rec_score >= 40:
        recommendation = "SHORTLIST"
    else:
        recommendation = "REJECT"

    _print_verdict(
        recommendation=recommendation,
        reason=data.get("recommendation_reason") or "",
        summary=data.get("summary") or "",
        qual_passed=qual_passed,
    )

    return BidEvaluation(
        bid=bid,
        qualification_passed=qual_passed,
        scores=scores,
        total_score=total_score,
        recommendation_score=rec_score,
        confidence=confidence,
        recommendation=recommendation,
        recommendation_reason=data.get("recommendation_reason") or "",
        summary=data.get("summary") or "",
    )