"""TypeSafe Jev (System One) question builders and answer readers.

Jev picks among options we already have. It does not generate prose. Callers
keep chat for search, alias expansion, and any path that must invent a string.
"""

from __future__ import annotations

from typing import Any

# Escape hatch on every Choice: none of the offered options fits.
NONE = "none"

# Choice confidence below this abstains into chat (when a chat path exists).
# Snapshot of members/elements LLM_CONFIDENCE_FLOOR.
CHOICE_CONFIDENCE_FLOOR = 0.8

# Noul bands: commit only when clearly yes or clearly no.
NOUL_YES = 0.8
NOUL_NO = 0.2

# Soft char clip so state + longest question stay under Jev's ~32k-token state
# budget. Roughly four chars per token of English prose.
STATE_CHAR_BUDGET = 100_000


def clip_state(state: str | dict[str, Any] | list[Any], *, budget: int = STATE_CHAR_BUDGET) -> str | dict | list:
    """Trim a state so it fits the System One context budget."""
    if isinstance(state, str):
        return state[:budget]
    if isinstance(state, list):
        text = "\n".join(str(item) for item in state)
        if len(text) <= budget:
            return state
        return text[:budget]
    if isinstance(state, dict):
        text = str(state)
        if len(text) <= budget:
            return state
        # Prefer keeping a `text` / `document` key when present.
        for key in ("text", "document", "quote", "context"):
            if key in state and isinstance(state[key], str):
                trimmed = dict(state)
                overhead = len(str({k: v for k, v in state.items() if k != key}))
                trimmed[key] = state[key][: max(0, budget - overhead)]
                return trimmed
        return str(state)[:budget]
    return str(state)[:budget]


def choice(*, instructions: str, criteria: dict[str, str | None]) -> dict[str, Any]:
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def noul(*, instructions: str, criteria: dict[str, str] | None = None) -> dict[str, Any]:
    q: dict[str, Any] = {"type": "noul", "instructions": instructions}
    if criteria:
        q["criteria"] = criteria
    return q


def score(*, instructions: str, criteria: list[str]) -> dict[str, Any]:
    return {"type": "score", "instructions": instructions, "criteria": criteria}


def choice_answer(answers: dict[str, Any], key: str) -> dict[str, Any] | None:
    """A Choice answer when confidence clears the floor; None to abstain."""
    raw = answers.get(key)
    if not isinstance(raw, dict) or raw.get("type") != "choice":
        return None
    conf = float(raw.get("confidence") or 0.0)
    if conf < CHOICE_CONFIDENCE_FLOOR:
        return None
    picked = raw.get("choice")
    if picked is None:
        return None
    return {
        "choice": str(picked),
        "confidence": conf,
        "probabilities": raw.get("probabilities") or {},
    }


def noul_answer(answers: dict[str, Any], key: str) -> dict[str, Any] | None:
    """A Noul answer committed to yes/no; None when in the middle band."""
    raw = answers.get(key)
    if not isinstance(raw, dict) or raw.get("type") != "noul":
        return None
    value = raw.get("noul")
    if value is None:
        return None
    p = float(value)
    if p >= NOUL_YES:
        return {"is_yes": True, "noul": p, "confidence": p}
    if p <= NOUL_NO:
        return {"is_yes": False, "noul": p, "confidence": 1.0 - p}
    return None


def with_none(criteria: dict[str, str | None], *, none_rubric: str) -> dict[str, str | None]:
    """Choice criteria plus the refuse option."""
    out = dict(criteria)
    out[NONE] = none_rubric
    return out
