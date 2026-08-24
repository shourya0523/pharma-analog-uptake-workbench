from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import yaml

from app.config import get_settings


PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


class OpenRouterClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    def _headers(self) -> dict[str, str]:
        if not self.settings.openrouter_api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set")
        return {
            "Authorization": f"Bearer {self.settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/pharma-analog-uptake-workbench",
            "X-Title": "Pharma Analog Uptake Workbench",
        }

    async def chat_json(self, *, model: str, system: str, user: str) -> dict[str, Any]:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        }
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self.settings.openrouter_base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)


def load_prompt(name: str) -> dict[str, Any]:
    path = PROMPTS_DIR / f"{name}.yaml"
    with path.open() as f:
        return yaml.safe_load(f)


class LLMModules:
    def __init__(self, client: OpenRouterClient | None = None) -> None:
        self.client = client or OpenRouterClient()
        self.settings = get_settings()

    async def extract_revenue(self, *, product: str, company: str | None, source_meta: dict, text: str) -> dict[str, Any]:
        prompt = load_prompt("revenue_extractor")
        user = prompt["user_template"].format(
            product=product,
            company=company or "",
            source_meta=json.dumps(source_meta),
            text=text[:50000],
        )
        if not self.settings.openrouter_api_key:
            return {"candidates": [], "note": "OPENROUTER_API_KEY missing; skipped LLM"}
        return await self.client.chat_json(
            model=self.settings.openrouter_model_extract,
            system=prompt["system"],
            user=user,
        )

    async def extract_metadata(self, *, product: str, text: str, source_meta: dict) -> dict[str, Any]:
        prompt = load_prompt("metadata_extractor")
        user = prompt["user_template"].format(
            product=product,
            source_meta=json.dumps(source_meta),
            text=text[:40000],
        )
        if not self.settings.openrouter_api_key:
            return {"fields": [], "note": "OPENROUTER_API_KEY missing; skipped LLM"}
        return await self.client.chat_json(
            model=self.settings.openrouter_model_extract,
            system=prompt["system"],
            user=user,
        )

    async def judge(self, *, candidate: dict, quote: str, context: str) -> dict[str, Any]:
        prompt = load_prompt("evidence_judge")
        user = prompt["user_template"].format(
            candidate=json.dumps(candidate),
            quote=quote,
            context=context[:8000],
        )
        if not self.settings.openrouter_api_key:
            return {
                "validation_status": "needs_review",
                "support_classification": "unknown",
                "issues": ["LLM judge unavailable"],
                "explanation": "OPENROUTER_API_KEY missing",
            }
        return await self.client.chat_json(
            model=self.settings.openrouter_model_judge,
            system=prompt["system"],
            user=user,
        )

    async def reconcile(self, *, candidates: list[dict]) -> dict[str, Any]:
        prompt = load_prompt("conflict_reconciler")
        user = prompt["user_template"].format(candidates=json.dumps(candidates)[:40000])
        if not self.settings.openrouter_api_key:
            return {"resolved": candidates, "conflicts": []}
        return await self.client.chat_json(
            model=self.settings.openrouter_model_judge,
            system=prompt["system"],
            user=user,
        )

    async def completeness(self, *, profile: dict, datapoints: list[dict], unresolved: list[dict]) -> dict[str, Any]:
        prompt = load_prompt("completeness_analyzer")
        user = prompt["user_template"].format(
            profile=json.dumps(profile),
            datapoints=json.dumps(datapoints)[:20000],
            unresolved=json.dumps(unresolved),
        )
        if not self.settings.openrouter_api_key:
            n = len(datapoints)
            u = len(unresolved)
            pct = round(100 * n / max(n + u, 1), 1)
            return {"completeness_pct": pct, "missing_periods": [x.get("period") for x in unresolved], "limitations": [], "recommended_next_steps": []}
        return await self.client.chat_json(
            model=self.settings.openrouter_model_extract,
            system=prompt["system"],
            user=user,
        )
