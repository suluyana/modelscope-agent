import json
import math
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from omegaconf import OmegaConf

from ms_agent.llm.llm import LLM
from ms_agent.llm.utils import Message
from ms_agent.self_improve.schemas import (
    DecisionResult,
    DecisionSource,
    IncidentClass,
    IncidentDetail,
    IncidentSignal,
)


class LLMArbiter:
    """Optional semantic arbiter for hybrid decision mode."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.llm = None
        llm_cfg = OmegaConf.create({"llm": config.get("llm", {"model": "qwen-max"})})
        try:
            self.llm = LLM.from_config(llm_cfg)
        except Exception as e:
            print(f"[DecisionEngine] LLM arbiter disabled: {e}")

    def _extract_text(self, response: Any) -> str:
        if isinstance(response, str):
            return response
        if hasattr(response, "content"):
            return response.content or ""
        return str(response)

    def _extract_json(self, text: str) -> Optional[Dict[str, Any]]:
        block = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        if block:
            try:
                return json.loads(block.group(1))
            except Exception:
                pass
        decoder = json.JSONDecoder()
        for i, ch in enumerate(text):
            if ch != "{":
                continue
            try:
                obj, _ = decoder.raw_decode(text[i:])
                if isinstance(obj, dict):
                    return obj
            except Exception:
                continue
        return None

    def _evidence_snippets(self, signal: IncidentSignal) -> str:
        snippets = []
        for ev in signal.evidence_index[:6]:
            path = Path(ev.path)
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if len(content) > 1600:
                content = content[-1600:]
            snippets.append(f"--- {ev.kind.value} from {path.name} ---\n{content}")
        if not snippets:
            return "No evidence snippets were available."
        return "\n\n".join(snippets)

    def arbitrate(
        self, signal: IncidentSignal, rule_primary: IncidentDetail
    ) -> Optional[Tuple[IncidentClass, float, str]]:
        if not self.llm:
            return None

        incidents = []
        for inc in signal.incidents[:5]:
            incidents.append(
                {
                    "class": inc.incident_class.value,
                    "confidence": inc.confidence,
                    "summary": inc.summary,
                }
            )

        prompt = (
            "You are a failure classification arbiter for a self-improvement framework.\n"
            "Given rule-based candidates, choose ONE final class and confidence.\n"
            "Return strict JSON with keys: class, confidence, reason.\n"
            "Allowed class values: infra_error, framework_error, task_solution_error, unknown.\n\n"
            f"Rule primary: class={rule_primary.incident_class.value}, confidence={rule_primary.confidence:.2f}\n"
            f"Run status={signal.status}, exit_code={signal.exit_code}, reward={signal.reward}\n"
            f"Candidates: {json.dumps(incidents, ensure_ascii=False)}\n"
            "Evidence snippets:\n"
            f"{self._evidence_snippets(signal)}\n"
        )
        messages = [
            Message(
                role="system",
                content="You arbitrate failure classes and output JSON only.",
            ),
            Message(role="user", content=prompt),
        ]

        try:
            response = self.llm.generate(messages, stream=False)
            text = self._extract_text(response)
            data = self._extract_json(text)
            if not data:
                return None
            cls_raw = str(data.get("class", "")).strip()
            if cls_raw not in {c.value for c in IncidentClass}:
                return None
            conf = float(data.get("confidence", 0.0))
            if not math.isfinite(conf):
                return None
            conf = max(0.0, min(1.0, conf))
            reason = str(data.get("reason", "")).strip() or "LLM arbitration"
            return IncidentClass(cls_raw), conf, reason
        except Exception as e:
            print(f"[DecisionEngine] LLM arbitration failed: {e}")
            return None


class DecisionEngine:
    def __init__(self, config: Dict[str, Any], run_mode: str):
        self.config = config
        self.run_mode = run_mode
        decision_cfg = config.get("decision", {})
        self.decision_mode = decision_cfg.get("mode", "rule_only")
        if self.decision_mode not in {"rule_only", "hybrid"}:
            raise ValueError(
                f"Invalid decision.mode={self.decision_mode!r}, expected 'rule_only' or 'hybrid'"
            )
        thresholds = decision_cfg.get("thresholds", {})
        classifier_cfg = decision_cfg.get("classifier", {})
        fallback_cfg = decision_cfg.get("fallback", {})

        self.rule_high_confidence = float(thresholds.get("rule_high_confidence", 0.90))
        self.llm_min_confidence = float(thresholds.get("llm_min_confidence", 0.70))
        self.disagreement_delta = float(thresholds.get("disagreement_delta", 0.25))
        self.min_confidence = float(classifier_cfg.get("min_confidence", 0.75))

        self.on_low_confidence = fallback_cfg.get("on_low_confidence", "switch_to_assist")
        self.on_rule_llm_disagreement = fallback_cfg.get(
            "on_rule_llm_disagreement", "ask_human"
        )

        self.arbiter = LLMArbiter(config) if self.decision_mode == "hybrid" else None

    def _low_confidence_mode(self) -> str:
        if self.on_low_confidence == "ask_human":
            return "ask_human"
        if self.on_low_confidence == "stop_with_report":
            return "observe"
        return "assist"

    def _suggest_mode(self, incident_class: IncidentClass, confidence: float) -> str:
        if incident_class == IncidentClass.UNKNOWN:
            return "observe"
        if confidence < self.min_confidence:
            return self._low_confidence_mode()

        if incident_class == IncidentClass.FRAMEWORK_ERROR:
            return self.run_mode
        if incident_class in (IncidentClass.INFRA_ERROR, IncidentClass.TASK_SOLUTION_ERROR):
            return "observe"
        return "observe"

    def _cap_mode_to_run_mode(self, mode: str) -> str:
        if mode == "ask_human":
            return "ask_human"
        if self.run_mode == "observe":
            return "observe"
        if self.run_mode == "assist" and mode == "auto":
            return "assist"
        return mode

    def _result(
        self,
        *,
        incident_class: IncidentClass,
        confidence: float,
        source: DecisionSource,
        suggested_mode: str,
        reason: str,
        fingerprint: Optional[str],
        rule_confidence: Optional[float] = None,
        llm_confidence: Optional[float] = None,
        class_conflict: bool = False,
        confidence_gap: Optional[float] = None,
    ) -> DecisionResult:
        return DecisionResult(
            decision_class=incident_class,
            decision_confidence=max(0.0, min(1.0, confidence)),
            decision_source=source,
            suggested_mode=self._cap_mode_to_run_mode(suggested_mode),
            reason=reason,
            incident_fingerprint=fingerprint,
            rule_confidence=rule_confidence,
            llm_confidence=llm_confidence,
            class_conflict=class_conflict,
            confidence_gap=confidence_gap,
        )

    def decide(self, signal: IncidentSignal) -> DecisionResult:
        primary = signal.primary_incident
        if not primary:
            return self._result(
                incident_class=IncidentClass.UNKNOWN,
                confidence=0.0,
                source=DecisionSource.RULE_ONLY,
                suggested_mode="observe",
                reason="No primary incident available.",
                fingerprint=None,
            )

        rule_class = primary.incident_class
        rule_conf = primary.confidence
        fingerprint = primary.fingerprint

        if self.decision_mode != "hybrid":
            mode = self._suggest_mode(rule_class, rule_conf)
            return self._result(
                incident_class=rule_class,
                confidence=rule_conf,
                source=DecisionSource.RULE_ONLY,
                suggested_mode=mode,
                reason=f"rule_only decision for {rule_class.value} ({rule_conf:.2f})",
                fingerprint=fingerprint,
                rule_confidence=rule_conf,
            )

        # Hybrid mode:
        if rule_conf >= self.rule_high_confidence:
            mode = self._suggest_mode(rule_class, rule_conf)
            return self._result(
                incident_class=rule_class,
                confidence=rule_conf,
                source=DecisionSource.RULE_DIRECT,
                suggested_mode=mode,
                reason=(
                    f"hybrid: rule confidence {rule_conf:.2f} >= "
                    f"{self.rule_high_confidence:.2f}, use rule directly"
                ),
                fingerprint=fingerprint,
                rule_confidence=rule_conf,
            )

        arb = self.arbiter.arbitrate(signal, primary) if self.arbiter else None
        if not arb:
            mode = self._suggest_mode(rule_class, rule_conf)
            return self._result(
                incident_class=rule_class,
                confidence=rule_conf,
                source=DecisionSource.RULE_DIRECT,
                suggested_mode=mode,
                reason="hybrid: LLM arbiter unavailable, fallback to rule decision",
                fingerprint=fingerprint,
                rule_confidence=rule_conf,
            )

        llm_class, llm_conf, llm_reason = arb
        class_conflict = llm_class != rule_class
        confidence_gap = abs(rule_conf - llm_conf)

        if (
            rule_class == IncidentClass.UNKNOWN
            and rule_conf < self.min_confidence
            and llm_conf >= self.llm_min_confidence
        ):
            mode = self._suggest_mode(llm_class, llm_conf)
            return self._result(
                incident_class=llm_class,
                confidence=llm_conf,
                source=DecisionSource.LLM_ARBITER,
                suggested_mode=mode,
                reason=(
                    "hybrid: low-confidence rule was unknown; "
                    f"using confident LLM classification ({llm_reason})"
                ),
                fingerprint=fingerprint,
                rule_confidence=rule_conf,
                llm_confidence=llm_conf,
                class_conflict=class_conflict,
                confidence_gap=confidence_gap,
            )

        if llm_conf < self.llm_min_confidence:
            mode = self._low_confidence_mode()
            return self._result(
                incident_class=rule_class,
                confidence=rule_conf,
                source=DecisionSource.MERGED,
                suggested_mode=mode,
                reason=(
                    f"hybrid: llm confidence {llm_conf:.2f} < {self.llm_min_confidence:.2f}; "
                    f"fallback to rule ({llm_reason})"
                ),
                fingerprint=fingerprint,
                rule_confidence=rule_conf,
                llm_confidence=llm_conf,
                class_conflict=class_conflict,
                confidence_gap=confidence_gap,
            )

        if class_conflict:
            if confidence_gap >= self.disagreement_delta:
                mode = (
                    "ask_human"
                    if self.on_rule_llm_disagreement == "ask_human"
                    else "assist"
                )
                return self._result(
                    incident_class=IncidentClass.UNKNOWN,
                    confidence=min(rule_conf, llm_conf),
                    source=DecisionSource.MERGED,
                    suggested_mode=mode,
                    reason=(
                        f"hybrid: class conflict ({rule_class.value} vs {llm_class.value}) "
                        f"with gap {confidence_gap:.2f}, escalate ({llm_reason})"
                    ),
                    fingerprint=fingerprint,
                    rule_confidence=rule_conf,
                    llm_confidence=llm_conf,
                    class_conflict=True,
                    confidence_gap=confidence_gap,
                )
            return self._result(
                incident_class=IncidentClass.UNKNOWN,
                confidence=min(rule_conf, llm_conf),
                source=DecisionSource.MERGED,
                suggested_mode="assist",
                reason=(
                    f"hybrid: class conflict ({rule_class.value} vs {llm_class.value}) "
                    f"with small gap {confidence_gap:.2f}, conservative unknown ({llm_reason})"
                ),
                fingerprint=fingerprint,
                rule_confidence=rule_conf,
                llm_confidence=llm_conf,
                class_conflict=True,
                confidence_gap=confidence_gap,
            )

        merged_conf = max(rule_conf, llm_conf)
        mode = self._suggest_mode(llm_class, merged_conf)
        return self._result(
            incident_class=llm_class,
            confidence=merged_conf,
            source=DecisionSource.LLM_ARBITER,
            suggested_mode=mode,
            reason=f"hybrid: rule and llm aligned ({llm_reason})",
            fingerprint=fingerprint,
            rule_confidence=rule_conf,
            llm_confidence=llm_conf,
            class_conflict=False,
            confidence_gap=confidence_gap,
        )
