"""Hermes-managed LLM research and analysis adapter for the graph.

Production calls use the plugin-provided ``ctx.llm`` completion facade. Hermes
owns authentication, provider routing, usage accounting, and fallbacks. The
legacy fake ``invoke`` seam is offline-test-only.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from .prompts import (
    ANALYSIS_PROMPT,
    MACRO_BACKGROUND_PROMPT,
    MARKET_IMPACT_PROMPT,
    QUALITY_GATE_PROMPT,
    RESEARCH_PROMPT,
)
logger = logging.getLogger(__name__)


def _usable_evidence(evidence: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Return only evidence items that have a non-empty content field."""
    if not evidence:
        return []
    return [e for e in evidence if e.get("content")]


def _format_evidence(evidence: list[dict[str, Any]]) -> str:
    if not evidence:
        return "(no usable evidence)"
    lines = []
    for item in evidence:
        url = item.get("url", "<no-url>")
        content = item.get("content", "")
        truncated = item.get("truncated", False)
        marker = " [truncated]" if truncated else ""
        lines.append(f"- {url}{marker}\n  {content}")
    return "\n".join(lines)


def _format_source_context(source_context: dict[str, Any]) -> str:
    if not source_context:
        return "(no source context)"
    parts = []
    for key in ("title", "summary", "url", "source_name"):
        value = source_context.get(key)
        if value:
            parts.append(f"{key}: {value}")
    return "\n".join(parts) if parts else str(source_context)


def _safe_parse_json(text: str) -> Optional[dict[str, Any]]:
    """Try to parse a JSON object from LLM output.

    Handles models that wrap the JSON in ```json ... ``` fences.
    Returns None if parsing fails.
    """
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline != -1:
            cleaned = cleaned[first_newline + 1 :]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    try:
        return json.loads(cleaned)
    except (ValueError, TypeError):
        pass
    # Fallback: models sometimes wrap the JSON in prose or a stray prefix.
    # Extract the outermost {...} object and try again before giving up.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except (ValueError, TypeError):
            return None
    return None


class ResearchAdapter:
    """Adapter that wraps an LLM to do research, analysis, and quality checks."""

    def __init__(
        self,
        temperature: float = 0.2,
        hermes_llm: Any | None = None,
        model_routes: dict[str, dict[str, Any]] | None = None,
        strict_model_routes: bool = True,
        llm_audit: list[dict[str, str]] | None = None,
    ) -> None:
        self.temperature = temperature
        self._hermes_llm = hermes_llm
        self._model_routes = model_routes or {}
        self._strict_model_routes = strict_model_routes
        self._llm_audit = llm_audit if llm_audit is not None else []
        self._fake_llm: Any = None

    def set_fake_llm(self, fake_llm: Any) -> None:
        """Inject an offline test double; production always uses ``ctx.llm``."""
        self._fake_llm = fake_llm

    def _route_kwargs(self, node: str) -> tuple[dict[str, str], dict[str, str] | None]:
        """Return Hermes overrides and the expected explicit route, if any."""
        route = self._model_routes.get(node) or {}
        if route.get("inherit_cron_model") or not route:
            return {}, None
        provider = str(route.get("provider") or "").strip()
        model = str(route.get("model") or "").strip()
        if not provider or not model:
            raise ValueError(f"model route for {node} must include provider and model")
        expected = {"provider": provider, "model": model}
        return dict(expected), expected

    def _invoke(self, prompt: str, *, node: str = "analysis") -> str:
        """Call the Hermes completion facade and audit its resolved route."""
        if self._fake_llm is not None:
            response = self._fake_llm.invoke(prompt)
            content = getattr(response, "content", response)
            if isinstance(content, list):
                return "".join(str(part) for part in content)
            return str(content)
        if self._hermes_llm is None:
            raise ValueError("ResearchAdapter requires Hermes ctx.llm for real LLM calls")

        overrides, expected = self._route_kwargs(node)
        response = self._hermes_llm.complete(
            messages=[{"role": "user", "content": prompt}], **overrides
        )
        provider = str(getattr(response, "provider", "") or "")
        model = str(getattr(response, "model", "") or "")
        if expected and self._strict_model_routes and (
            provider != expected["provider"] or model != expected["model"]
        ):
            raise RuntimeError(
                "Unexpected LLM route: "
                f"expected {expected['provider']}/{expected['model']}, "
                f"got {provider}/{model}"
            )
        self._llm_audit.append({"node": node, "provider": provider, "model": model})
        content = getattr(response, "text", getattr(response, "content", response))
        if isinstance(content, list):
            return "".join(str(part) for part in content)
        return str(content)
    def analyze(
        self,
        title: str,
        source_context: dict[str, Any],
        evidence: list[dict[str, Any]],
        relevance_score: float,
        language: str = "zh-CN",
    ) -> dict[str, Any]:
        """Generate an evidence-grounded analysis.

        When there is no usable evidence, still attempts a lightweight LLM
        call using just the title and source context, so items without
        evidence can still receive basic signals.
        """
        usable = _usable_evidence(evidence)
        evidence_text = _format_evidence(usable) if usable else "(no evidence available)"

        prompt = ANALYSIS_PROMPT.format(
            title=title or "",
            source_context=_format_source_context(source_context),
            evidence=evidence_text,
            relevance_score=relevance_score,
            language=language,
        )

        try:
            raw = self._invoke(prompt)
        except Exception as exc:
            logger.exception("research analyze llm call failed title=%s", title)
            return {
                "analysis": "",
                "signals": [],
                "focus_assets": [],
                "requires_agent_enrichment": True,
                "error": f"llm error: {exc}",
            }

        parsed = _safe_parse_json(raw)
        # The model is non-deterministic: it occasionally returns prose or
        # malformed JSON with no signals. Retry once with a stricter reminder
        # before giving up, so a single bad roll doesn't leave the item with
        # an empty "待补充" signal.
        if not parsed or not parsed.get("signals"):
            try:
                raw_retry = self._invoke(
                    prompt + "\n\n再次提醒：只输出一个严格的 JSON 对象，不要任何解释或 markdown。"
                )
                parsed_retry = _safe_parse_json(raw_retry)
                if parsed_retry and parsed_retry.get("signals"):
                    parsed, raw = parsed_retry, raw_retry
            except Exception:
                logger.warning("research analyze retry failed title=%s", title)

        parsed = parsed or {}
        analysis_text = parsed.get("analysis") or raw
        signals = parsed.get("signals") or []
        focus_assets = parsed.get("focus_assets") or []
        translated_title = str(parsed.get("translated_title") or "").strip()
        translated_summary = str(parsed.get("translated_summary") or "").strip()

        if not isinstance(signals, list):
            signals = [str(signals)]
        if not isinstance(focus_assets, list):
            focus_assets = [str(focus_assets)]

        # If we still have no signals, flag the item so the auto-enrich step
        # (and downstream) know it needs backfilling rather than silently
        # rendering "待基于已核验来源补充".
        if not signals:
            return {
                "analysis": str(analysis_text),
                "signals": [],
                "focus_assets": focus_assets if isinstance(focus_assets, list) else [],
                "translated_title": translated_title,
                "translated_summary": translated_summary,
                "requires_agent_enrichment": True,
                "error": "llm returned no parseable signals",
            }

        return {
            "analysis": str(analysis_text),
            "signals": signals if isinstance(signals, list) else [],
            "focus_assets": focus_assets if isinstance(focus_assets, list) else [],
            "translated_title": translated_title,
            "translated_summary": translated_summary,
            "requires_agent_enrichment": False,
            "error": None,
        }

    def research(
        self,
        source_context: dict[str, Any],
        evidence: list[dict[str, Any]],
        language: str = "zh-CN",
    ) -> dict[str, Any]:
        """Summarise evidence into structured facts / inference / uncertainty."""
        usable = _usable_evidence(evidence)
        if not usable:
            return {
                "facts": [],
                "inference": "",
                "uncertainty": "",
                "evidence": evidence,
                "error": "no usable evidence",
            }

        prompt = RESEARCH_PROMPT.format(
            source_context=_format_source_context(source_context),
            evidence=_format_evidence(usable),
            language=language,
        )

        try:
            raw = self._invoke(prompt, node="research")
        except Exception as exc:
            logger.exception("research summary llm call failed")
            return {
                "facts": [],
                "inference": "",
                "uncertainty": "",
                "evidence": evidence,
                "error": f"llm error: {exc}",
            }

        parsed = _safe_parse_json(raw) or {}
        facts = parsed.get("facts") or parsed.get("关键事实") or []
        if not isinstance(facts, list):
            facts = [str(facts)]
        inference = parsed.get("inference") or parsed.get("市场推断") or ""
        uncertainty = parsed.get("uncertainty") or parsed.get("不确定性") or ""

        return {
            "facts": [str(f) for f in facts],
            "inference": str(inference),
            "uncertainty": str(uncertainty),
            "evidence": evidence,
            "error": None,
        }

    MARKET_IMPACT_KEYS = ("rates_bonds", "fx", "equities", "commodities", "risk_appetite")

    def market_impact(self, signals_digest: str, language: str = "zh-CN") -> dict[str, str]:
        """Synthesise the 5-dimension market-impact map from the day's signals.

        Returns a dict with all five required keys filled, or ``{}`` when the
        digest is empty, the LLM call fails, or any key comes back blank — the
        caller is expected to fall back to the deterministic placeholder map.
        """
        if not signals_digest.strip():
            return {}
        prompt = MARKET_IMPACT_PROMPT.format(
            signals_digest=signals_digest, language=language
        )
        try:
            raw = self._invoke(prompt, node="market_impact")
        except Exception:
            logger.exception("market_impact llm call failed")
            return {}
        parsed = _safe_parse_json(raw) or {}
        result = {key: str(parsed.get(key, "")).strip() for key in self.MARKET_IMPACT_KEYS}
        if not all(result.values()):
            return {}
        return result

    MACRO_BACKGROUND_KEYS = ("CN", "US", "JP")

    def macro_background(self, official_digest: str, language: str = "zh-CN") -> dict[str, str]:
        """Synthesise the 中美日 macro-background narrative from official items.

        Returns ``{country_code: text}`` for whichever of CN/US/JP came back
        non-empty, or ``{}`` when the digest is empty, the LLM call fails, or
        nothing parsed — the caller then falls back to the deterministic,
        title-based macro background built by ``briefing_builder``.
        """
        if not official_digest.strip():
            return {}
        prompt = MACRO_BACKGROUND_PROMPT.format(
            official_digest=official_digest, language=language
        )
        try:
            raw = self._invoke(prompt, node="macro_background")
        except Exception:
            logger.exception("macro_background llm call failed")
            return {}
        parsed = _safe_parse_json(raw) or {}
        result = {key: str(parsed.get(key, "")).strip() for key in self.MACRO_BACKGROUND_KEYS}
        if not any(result.values()):
            return {}
        return {key: value for key, value in result.items() if value}

    def quality_check(
        self,
        title: str,
        analysis: str,
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Run the quality gate. Returns ``{verdict, reason, error}``."""
        usable = _usable_evidence(evidence)
        if not usable:
            return {
                "verdict": "FAIL",
                "reason": "no usable evidence",
                "error": None,
            }

        prompt = QUALITY_GATE_PROMPT.format(
            title=title or "",
            evidence=_format_evidence(usable),
            analysis=analysis or "",
        )

        try:
            raw = self._invoke(prompt, node="quality_check")
        except Exception as exc:
            logger.exception("quality check llm call failed title=%s", title)
            return {
                "verdict": None,
                "reason": "",
                "error": f"llm error: {exc}",
            }

        parsed = _safe_parse_json(raw) or {}
        verdict = parsed.get("verdict")
        if verdict not in ("PASS", "FAIL"):
            text_upper = raw.upper()
            if "PASS" in text_upper and "FAIL" not in text_upper:
                verdict = "PASS"
            elif "FAIL" in text_upper:
                verdict = "FAIL"
            else:
                verdict = "FAIL"
        reason = parsed.get("reason", "")

        return {
            "verdict": verdict,
            "reason": str(reason),
            "error": None,
        }
