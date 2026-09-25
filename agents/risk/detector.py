import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from agents.knowledge.schemas import InteractionEvidence
from agents.knowledge.service import KnowledgeService
from agents.risk.schemas import Finding, RiskAssessmentReport

DEMO_PACK_PATH = Path(__file__).resolve().parent.parent / "rules" / "packs" / "aegis_hackathon_demo_v1.json"

ACE_INHIBITOR_NAMES = frozenset(
    {
        "lisinopril",
        "enalapril",
        "ramipril",
        "captopril",
        "benazepril",
        "fosinopril",
        "quinapril",
        "perindopril",
        "trandolapril",
        "moexipril",
    }
)


class RiskDetector:
    """
    Deterministic risk detection engine evaluating complete patient clinical context:
    - Active medications & medication orders/status
    - Relevant labs & chronological lab trends
    - Validated safety rules (drug-drug interaction, renal risk, duplicate therapy)
    - Missing clinical data handling (never guesses or assumes unvalidated values).
    """

    def __init__(
        self,
        knowledge_service: KnowledgeService,
        patient_context: Optional[Any] = None,
        medications: Optional[List[Any]] = None,
        labs: Optional[List[Any]] = None,
        orders: Optional[List[Any]] = None,
        rule_context: Optional[Any] = None,
    ):
        self.knowledge_service = knowledge_service
        self.patient_context = patient_context
        self.medications = medications or []
        self.labs = labs or []
        self.orders = orders or []
        self.rule_context = rule_context

        # Load demo rules from package if present
        self._demo_rules: List[Dict[str, Any]] = []
        if DEMO_PACK_PATH.exists():
            try:
                with open(DEMO_PACK_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._demo_rules = data.get("rules", [])
            except Exception:
                self._demo_rules = []

    def _extract_drug_name(self, med: Any) -> Optional[str]:
        """Safely extract drug name from ORM model, Pydantic schema, dict, or string."""
        if isinstance(med, str):
            name = med.strip()
            return name if name else None

        if isinstance(med, dict):
            status = med.get("status", "active")
            if status and str(status).lower() in {"inactive", "discontinued", "cancelled", "completed"}:
                return None
            name = med.get("drug_name") or med.get("name")
            return str(name).strip() if name else None

        # ORM model or Pydantic schema object
        status = getattr(med, "status", "active")
        if status and str(status).lower() in {"inactive", "discontinued", "cancelled", "completed"}:
            return None

        name = getattr(med, "drug_name", None) or getattr(med, "name", None)
        return str(name).strip() if name else None

    def _extract_patient_id(self, patient_context: Any) -> Optional[Any]:
        """Extract patient identifier or ID from patient context."""
        if patient_context is None:
            return None
        if isinstance(patient_context, (int, str)):
            return patient_context
        if isinstance(patient_context, dict):
            return patient_context.get("id") or patient_context.get("patient_identifier")
        return getattr(patient_context, "id", None) or getattr(patient_context, "patient_identifier", None)

    def _extract_labs(self, labs: Optional[List[Any]]) -> List[Dict[str, Any]]:
        """Normalize lab records into clean dicts sorted chronologically."""
        if not labs:
            return []

        clean_labs: List[Dict[str, Any]] = []
        for lab in labs:
            if lab is None:
                continue
            if isinstance(lab, dict):
                test_name = str(lab.get("test_name") or lab.get("name") or "").strip()
                val = str(lab.get("value") or "").strip()
                unit = lab.get("unit")
                measured_at = lab.get("measured_at") or lab.get("created_at")
                lab_id = lab.get("id", 0)
            else:
                test_name = str(getattr(lab, "test_name", None) or getattr(lab, "name", "")).strip()
                val = str(getattr(lab, "value", "")).strip()
                unit = getattr(lab, "unit", None)
                measured_at = getattr(lab, "measured_at", None) or getattr(lab, "created_at", None)
                lab_id = getattr(lab, "id", 0)

            if not test_name:
                continue

            num_val = None
            try:
                # Extract leading float if present
                clean_num_str = re.findall(r"[-+]?\d*\.?\d+", val)
                if clean_num_str:
                    num_val = float(clean_num_str[0])
            except Exception:
                num_val = None

            clean_labs.append(
                {
                    "id": lab_id,
                    "test_name": test_name.lower(),
                    "raw_test_name": test_name,
                    "value": val,
                    "numeric_value": num_val,
                    "unit": unit,
                    "measured_at": measured_at,
                }
            )

        # Sort chronologically by measured_at/id
        clean_labs.sort(key=lambda x: (str(x["measured_at"] or ""), x["id"]))
        return clean_labs

    def _analyze_lab_trend(
        self,
        clean_labs: List[Dict[str, Any]],
        test_keywords: List[str],
    ) -> Dict[str, Any]:
        """
        Analyze chronological trend for a set of test keywords without fabricating values.
        """
        matching = [
            l for l in clean_labs if any(kw in l["test_name"] for kw in test_keywords)
        ]
        if not matching:
            return {"status": "missing", "labs_count": 0, "history": []}

        latest = matching[-1]
        trend = "single_value"
        if len(matching) >= 2:
            prev = matching[-2]
            if prev["numeric_value"] is not None and latest["numeric_value"] is not None:
                if latest["numeric_value"] > prev["numeric_value"]:
                    trend = "rising"
                elif latest["numeric_value"] < prev["numeric_value"]:
                    trend = "declining"
                else:
                    trend = "stable"
            else:
                trend = "unspecified_trend"

        return {
            "status": "present",
            "test_name": latest["raw_test_name"],
            "latest_value": latest["value"],
            "latest_numeric": latest["numeric_value"],
            "latest_unit": latest["unit"],
            "trend": trend,
            "labs_count": len(matching),
            "history": [
                {
                    "value": l["value"],
                    "unit": l["unit"],
                    "measured_at": str(l["measured_at"] or ""),
                }
                for l in matching
            ],
        }

    def _is_ace_inhibitor(self, drug_name: str) -> bool:
        """Deterministically determine if a drug belongs to the ACE inhibitor class."""
        clean = re.sub(r"[^a-zA-Z\s-]", "", drug_name).lower().strip()
        words = clean.split()
        for w in words:
            if w in ACE_INHIBITOR_NAMES or w.endswith("pril"):
                return True
        return "ace inhibitor" in clean or "ace-inhibitor" in clean

    def _get_applicable_rules(self, rule_context: Optional[Any] = None) -> List[Any]:
        """Gather active safety rules from rule_context and bundled demo pack."""
        rules: List[Any] = []
        ctx = rule_context if rule_context is not None else self.rule_context

        if ctx:
            if hasattr(ctx, "get_active_rules") and callable(ctx.get_active_rules):
                rules.extend(ctx.get_active_rules())
            elif hasattr(ctx, "rules"):
                rules.extend(ctx.rules)
            elif isinstance(ctx, list):
                rules.extend(ctx)
            elif isinstance(ctx, dict):
                rules.extend(ctx.values())
        else:
            rules.extend(self._demo_rules)

        # Deduplicate rules by rule_id
        seen_ids = set()
        deduped = []
        for r in rules:
            r_id = getattr(r, "rule_id", None) or (r.get("rule_id") if isinstance(r, dict) else None)
            if r_id and r_id not in seen_ids:
                seen_ids.add(r_id)
                deduped.append(r)
        return deduped

    @staticmethod
    def is_finding_unchanged(finding: Finding, previous_finding: Any) -> bool:
        """Deterministically check if a finding's clinical inputs, severity, and description are unchanged."""
        prev_inputs = (
            previous_finding.get("inputs")
            if isinstance(previous_finding, dict)
            else getattr(previous_finding, "inputs", {})
        ) or {}
        if isinstance(prev_inputs, str):
            try:
                prev_inputs = json.loads(prev_inputs)
            except Exception:
                prev_inputs = {}

        prev_desc = (
            previous_finding.get("description")
            if isinstance(previous_finding, dict)
            else getattr(previous_finding, "description", "")
        )
        prev_severity = (
            previous_finding.get("severity")
            if isinstance(previous_finding, dict)
            else getattr(previous_finding, "severity", "")
        )
        return (
            finding.description == prev_desc
            and finding.inputs == prev_inputs
            and finding.severity == prev_severity
        )

    def filter_unchanged_findings(
        self,
        current_findings: List[Finding],
        previous_findings: List[Any],
    ) -> List[Finding]:
        """
        Deduplicates current findings against previous findings.
        Returns only findings that are new or whose clinical context/inputs have changed.
        """
        if not previous_findings:
            return list(current_findings)

        prev_by_sig: Dict[Tuple[str, Tuple[str, ...]], Any] = {}
        for pf in previous_findings:
            rule_id = str(
                pf.get("rule_id") if isinstance(pf, dict) else getattr(pf, "rule_id", "")
            )
            trace = (
                pf.get("trace") if isinstance(pf, dict) else getattr(pf, "trace", {})
            ) or {}
            if isinstance(trace, str):
                try:
                    trace = json.loads(trace)
                except Exception:
                    trace = {}
            matched = tuple(
                sorted(str(d).lower().strip() for d in trace.get("matched_pair", []))
            )
            prev_by_sig[(rule_id, matched)] = pf

        filtered: List[Finding] = []
        for cf in current_findings:
            matched = tuple(
                sorted(str(d).lower().strip() for d in cf.trace.get("matched_pair", []))
            )
            sig = (str(cf.rule_id), matched)

            if sig not in prev_by_sig:
                filtered.append(cf)
            else:
                prev = prev_by_sig[sig]
                if not self.is_finding_unchanged(cf, prev):
                    filtered.append(cf)

        return filtered

    def detect(
        self,
        patient_context: Optional[Any] = None,
        medications: Optional[List[Any]] = None,
        labs: Optional[List[Any]] = None,
        orders: Optional[List[Any]] = None,
        previous_findings: Optional[List[Any]] = None,
        rule_context: Optional[Any] = None,
        filter_unchanged: bool = False,
    ) -> List[Finding]:
        """
        Execute deterministic risk detection for complete patient context.

        Evaluates:
        1. Pair-wise drug-drug interactions from KnowledgeService.
        2. Validated safety rules from RulePackService / demo pack:
           - Warfarin + Fluconazole with chronological INR context
           - Enoxaparin with renal-context change or missing baseline renal panel
           - Duplicate ACE-inhibitor therapy
        3. Explicit missing clinical data states (never guesses or fabricates).

        Returns:
            List[Finding]: List of identified safety findings with preserved provenance.
        """
        ctx = patient_context if patient_context is not None else self.patient_context
        med_list = medications if medications is not None else self.medications
        lab_list = labs if labs is not None else self.labs
        order_list = orders if orders is not None else self.orders

        patient_id = self._extract_patient_id(ctx)
        clean_labs = self._extract_labs(lab_list)
        normalizer = self.knowledge_service.normalizer

        # Determine discontinued / cancelled drugs from orders
        discontinued_by_order: Set[str] = set()
        active_order_drugs: List[str] = []
        if order_list:
            for ord_item in order_list:
                ord_status = (
                    ord_item.get("status")
                    if isinstance(ord_item, dict)
                    else getattr(ord_item, "status", None)
                )
                ord_name = (
                    ord_item.get("drug_name") or ord_item.get("name")
                    if isinstance(ord_item, dict)
                    else (getattr(ord_item, "drug_name", None) or getattr(ord_item, "name", None))
                )
                if ord_name:
                    norm_ord = normalizer.normalize(str(ord_name)).lower().strip()
                    if ord_status and str(ord_status).lower() in {"inactive", "discontinued", "cancelled", "completed"}:
                        discontinued_by_order.add(norm_ord)
                    elif ord_status in {"active", "ordered", "pending", None} or not ord_status:
                        active_order_drugs.append(str(ord_name).strip())

        # Extract active drug names from med_list
        all_active_meds: List[str] = []
        for item in med_list:
            d_name = self._extract_drug_name(item)
            if d_name:
                norm_d = normalizer.normalize(d_name).lower().strip()
                if norm_d not in discontinued_by_order:
                    all_active_meds.append(d_name)

        # Add active order drugs not discontinued
        for ord_d in active_order_drugs:
            norm_ord = normalizer.normalize(ord_d).lower().strip()
            if norm_ord not in discontinued_by_order:
                all_active_meds.append(ord_d)

        # Build unique normalized active drugs list
        active_drugs: List[str] = []
        seen_active_norm: Set[str] = set()
        for d in all_active_meds:
            norm_d = normalizer.normalize(d).lower().strip()
            if norm_d not in seen_active_norm:
                seen_active_norm.add(norm_d)
                active_drugs.append(d)

        if not active_drugs and not all_active_meds:
            return []

        findings: List[Finding] = []
        seen_finding_keys: Set[Tuple[str, Tuple[str, ...]]] = set()
        seen_drug_pairs: Set[Tuple[str, ...]] = set()

        # ---------------------------------------------------------
        # 1. KnowledgeService Pairwise Interactions (DDInter/OpenFDA)
        # ---------------------------------------------------------
        if len(active_drugs) >= 2:
            for i in range(len(active_drugs)):
                for j in range(i + 1, len(active_drugs)):
                    drug_a = active_drugs[i]
                    drug_b = active_drugs[j]

                    norm_a = normalizer.normalize(drug_a).lower().strip()
                    norm_b = normalizer.normalize(drug_b).lower().strip()

                    if not norm_a or not norm_b or norm_a == norm_b:
                        continue

                    interactions = self.knowledge_service.get_interaction_pair(drug_a, drug_b)
                    for evidence in interactions:
                        ev_a = normalizer.normalize(evidence.drug_a).lower().strip()
                        ev_b = normalizer.normalize(evidence.drug_b).lower().strip()

                        evidence_key = evidence.evidence_id or f"{ev_a}_{ev_b}_{evidence.description[:30]}"
                        pair_sig = tuple(sorted([norm_a, norm_b]))
                        finding_dedup = (str(evidence_key), pair_sig)

                        if finding_dedup in seen_finding_keys:
                            continue
                        seen_finding_keys.add(finding_dedup)
                        seen_drug_pairs.add(pair_sig)

                        rule_id = (
                            str(evidence.evidence_id)
                            if evidence.evidence_id
                            else f"DDI_{norm_a}_{norm_b}"
                        )

                        inputs_dict: Dict[str, Any] = {
                            "drug_a": drug_a,
                            "drug_b": drug_b,
                            "patient_id": patient_id,
                            "lab_count": len(clean_labs),
                        }

                        # If pair is Warfarin + Fluconazole, enrich with relevant INR context
                        desc = evidence.description
                        if {norm_a, norm_b} == {"fluconazole", "warfarin"}:
                            inr_trend = self._analyze_lab_trend(
                                clean_labs, ["inr", "international normalized ratio", "prothrombin"]
                            )
                            if inr_trend["status"] == "missing":
                                inputs_dict["inr_context"] = "missing"
                                desc += (
                                    "\n\nClinical Context: Missing recent INR lab on record. "
                                    "Baseline INR monitoring and clinical review required before co-administration."
                                )
                            else:
                                inputs_dict["inr_context"] = "present"
                                inputs_dict["inr_value"] = inr_trend["latest_value"]
                                inputs_dict["inr_trend"] = inr_trend["trend"]
                                inputs_dict["inr_history"] = inr_trend["history"]
                                desc += (
                                    f"\n\nClinical Context: Recent INR is {inr_trend['latest_value']} "
                                    f"with {inr_trend['trend']} trend across {inr_trend['labs_count']} lab record(s)."
                                )

                        finding = Finding(
                            rule_id=rule_id,
                            severity="undetermined",
                            title=f"Documented Interaction: {drug_a} + {drug_b}",
                            description=desc,
                            action=None,
                            inputs=inputs_dict,
                            trace={
                                "rule_id": rule_id,
                                "evidence_id": evidence.evidence_id,
                                "source": evidence.source,
                                "source_version": evidence.source_version,
                                "matched_pair": sorted([norm_a, norm_b]),
                            },
                            source=evidence.source,
                        )
                        findings.append(finding)

        # ---------------------------------------------------------
        # 2. Validated RulePack / Demo Safety Rules Evaluation
        # ---------------------------------------------------------
        rules = self._get_applicable_rules(rule_context)
        for rule in rules:
            r_id = getattr(rule, "rule_id", None) or (rule.get("rule_id") if isinstance(rule, dict) else "")
            r_type = getattr(rule, "rule_type", None) or (rule.get("rule_type") if isinstance(rule, dict) else "")
            source = getattr(rule, "source", None) or (rule.get("source") if isinstance(rule, dict) else "AEGIS_HACKATHON_DEMO")
            source_ver = getattr(rule, "source_version", None) or (rule.get("source_version") if isinstance(rule, dict) else "1.0.0")
            ev_id = getattr(rule, "evidence_id", None) or (rule.get("evidence_id") if isinstance(rule, dict) else "")
            ev_text = getattr(rule, "evidence_text", None) or (rule.get("evidence_text") if isinstance(rule, dict) else "")
            severity = getattr(rule, "severity", None) or (rule.get("severity") if isinstance(rule, dict) else "review_required")
            action = getattr(rule, "action", None) or (rule.get("action") if isinstance(rule, dict) else None)
            drug_a_rule = getattr(rule, "drug_a", None) or (rule.get("drug_a") if isinstance(rule, dict) else None)
            drug_b_rule = getattr(rule, "drug_b", None) or (rule.get("drug_b") if isinstance(rule, dict) else None)

            # DEMO CASE 1: Warfarin + Fluconazole with INR context
            if r_id == "AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE" or (
                drug_a_rule and drug_b_rule and {str(drug_a_rule).lower(), str(drug_b_rule).lower()} == {"warfarin", "fluconazole"}
            ):
                has_warfarin = any("warfarin" in normalizer.normalize(d).lower() for d in active_drugs)
                has_fluconazole = any("fluconazole" in normalizer.normalize(d).lower() for d in active_drugs)

                if has_warfarin and has_fluconazole:
                    pair_key = ("fluconazole", "warfarin")
                    if pair_key in seen_drug_pairs:
                        continue
                    sig = (str(r_id), pair_key)
                    if sig in seen_finding_keys:
                        continue
                    seen_finding_keys.add(sig)
                    seen_drug_pairs.add(pair_key)

                    inr_trend = self._analyze_lab_trend(
                        clean_labs, ["inr", "international normalized ratio", "prothrombin"]
                    )

                    inputs: Dict[str, Any] = {
                        "drug_a": "warfarin",
                        "drug_b": "fluconazole",
                        "patient_id": patient_id,
                        "lab_count": len(clean_labs),
                    }

                    if inr_trend["status"] == "missing":
                        inputs["inr_context"] = "missing"
                        inputs["data_needed"] = ["INR"]
                        desc = (
                            f"{ev_text}\n\nClinical Context: Missing recent INR lab on record. "
                            "Baseline INR monitoring and clinical review required before co-administration."
                        )
                        act = "Baseline INR monitoring and clinical review required before co-administration."
                    else:
                        inputs["inr_context"] = "present"
                        inputs["inr_value"] = inr_trend["latest_value"]
                        inputs["inr_trend"] = inr_trend["trend"]
                        inputs["inr_history"] = inr_trend["history"]
                        desc = (
                            f"{ev_text}\n\nClinical Context: Recent INR is {inr_trend['latest_value']} "
                            f"with {inr_trend['trend']} trend across {inr_trend['labs_count']} lab record(s)."
                        )
                        act = action or "review/hold/monitor; evaluate safer alternative"

                    finding = Finding(
                        rule_id=str(r_id),
                        severity=severity or "review_required",
                        title="Documented Interaction: warfarin + fluconazole",
                        description=desc,
                        action=act,
                        inputs=inputs,
                        trace={
                            "rule_id": str(r_id),
                            "evidence_id": str(ev_id),
                            "source": str(source),
                            "source_version": str(source_ver),
                            "matched_pair": ["fluconazole", "warfarin"],
                        },
                        source=str(source),
                    )
                    findings.append(finding)

            # DEMO CASE 2: Enoxaparin with renal-context change
            elif r_id == "AEGIS-DEMO-002-ENOXAPARIN-RENAL" or (
                r_type == "renal_risk" and (not drug_a_rule or "enoxaparin" in str(drug_a_rule).lower())
            ):
                has_enoxaparin = any("enoxaparin" in normalizer.normalize(d).lower() for d in active_drugs)
                if has_enoxaparin:
                    sig = (str(r_id), ("enoxaparin",))
                    if sig in seen_finding_keys:
                        continue
                    seen_finding_keys.add(sig)

                    renal_trend = self._analyze_lab_trend(
                        clean_labs, ["creatinine", "egfr", "crcl", "bun", "renal"]
                    )

                    inputs = {
                        "drug_a": "enoxaparin",
                        "patient_id": patient_id,
                        "lab_count": len(clean_labs),
                    }

                    if renal_trend["status"] == "missing":
                        inputs["renal_context"] = "missing"
                        inputs["data_needed"] = ["eGFR", "creatinine"]
                        desc = (
                            f"{ev_text}\n\nClinical Context: Missing baseline renal function panel (eGFR/Creatinine). "
                            "Clinical renal evaluation required."
                        )
                        act = "Renal function assessment and clinical dose evaluation required."
                    else:
                        renal_status = "declining" if renal_trend["trend"] == "rising" else renal_trend["trend"]
                        inputs["renal_context"] = renal_status
                        inputs["latest_renal_lab"] = renal_trend["latest_value"]
                        inputs["latest_test_name"] = renal_trend.get("test_name", "renal lab")
                        inputs["renal_history"] = renal_trend["history"]
                        desc = (
                            f"{ev_text}\n\nClinical Context: Renal function panel evaluated "
                            f"(latest {renal_trend.get('test_name', 'renal lab')}: {renal_trend['latest_value']}, "
                            f"trend: {renal_status} across {renal_trend['labs_count']} lab(s))."
                        )
                        act = action or "review/hold/monitor; evaluate dose adjustment"

                    finding = Finding(
                        rule_id=str(r_id),
                        severity=severity or "review_required",
                        title="Renal Risk Alert: enoxaparin with renal context",
                        description=desc,
                        action=act,
                        inputs=inputs,
                        trace={
                            "rule_id": str(r_id),
                            "evidence_id": str(ev_id),
                            "source": str(source),
                            "source_version": str(source_ver),
                            "matched_pair": ["enoxaparin"],
                        },
                        source=str(source),
                    )
                    findings.append(finding)

            elif r_type == "renal_risk" and drug_a_rule:
                norm_rule_drug = normalizer.normalize(str(drug_a_rule)).lower().strip()
                has_drug = any(norm_rule_drug in normalizer.normalize(d).lower() for d in active_drugs)
                if has_drug:
                    sig = (str(r_id), (norm_rule_drug,))
                    if sig in seen_finding_keys:
                        continue
                    seen_finding_keys.add(sig)

                    renal_trend = self._analyze_lab_trend(
                        clean_labs, ["creatinine", "egfr", "crcl", "bun", "renal"]
                    )
                    inputs = {
                        "drug_a": norm_rule_drug,
                        "patient_id": patient_id,
                        "lab_count": len(clean_labs),
                    }
                    if renal_trend["status"] == "missing":
                        inputs["renal_context"] = "missing"
                        inputs["data_needed"] = ["eGFR", "creatinine"]
                        desc = (
                            f"{ev_text or 'Renal safety rule triggered.'}\n\nClinical Context: Missing baseline renal function panel (eGFR/Creatinine). "
                            "Clinical renal evaluation required."
                        )
                        act = "Renal function assessment and clinical dose evaluation required."
                    else:
                        renal_status = "declining" if renal_trend["trend"] == "rising" else renal_trend["trend"]
                        inputs["renal_context"] = renal_status
                        inputs["latest_renal_lab"] = renal_trend["latest_value"]
                        inputs["latest_test_name"] = renal_trend.get("test_name", "renal lab")
                        inputs["renal_history"] = renal_trend["history"]
                        desc = (
                            f"{ev_text or 'Renal safety rule evaluated.'}\n\nClinical Context: Renal function panel evaluated "
                            f"(latest {renal_trend.get('test_name', 'renal lab')}: {renal_trend['latest_value']}, "
                            f"trend: {renal_status} across {renal_trend['labs_count']} lab(s))."
                        )
                        act = action or "review/hold/monitor; evaluate dose adjustment"

                    finding = Finding(
                        rule_id=str(r_id),
                        severity=severity or "review_required",
                        title=f"Renal Risk Alert: {norm_rule_drug} with renal context",
                        description=desc,
                        action=act,
                        inputs=inputs,
                        trace={
                            "rule_id": str(r_id),
                            "evidence_id": str(ev_id),
                            "source": str(source),
                            "source_version": str(source_ver),
                            "matched_pair": [norm_rule_drug],
                        },
                        source=str(source),
                    )
                    findings.append(finding)

            # DEMO CASE 3: Duplicate ACE-inhibitor therapy
            elif r_id == "AEGIS-DEMO-003-DUPLICATE-ACE-INHIBITOR" or (
                r_type == "duplicate_therapy"
                and (
                    not drug_a_rule
                    or str(drug_a_rule).lower() in {"ace inhibitor", "ace-inhibitor", "ace_inhibitor"}
                    or str(drug_b_rule).lower() in {"ace inhibitor", "ace-inhibitor", "ace_inhibitor"}
                )
            ):
                active_aces = [d for d in all_active_meds if self._is_ace_inhibitor(d)]
                if len(active_aces) >= 2:
                    sig = (str(r_id), tuple(sorted(str(a).lower() for a in active_aces[:2])))
                    if sig in seen_finding_keys:
                        continue
                    seen_finding_keys.add(sig)

                    inputs = {
                        "drug_a": active_aces[0],
                        "drug_b": active_aces[1],
                        "duplicate_class": "ace_inhibitor",
                        "active_ace_inhibitors": active_aces,
                        "patient_id": patient_id,
                        "lab_count": len(clean_labs),
                    }
                    desc = (
                        f"{ev_text}\n\nClinical Context: Multiple active ACE inhibitors detected "
                        f"({', '.join(active_aces)}). Concomitant therapy increases toxicity without additive efficacy."
                    )
                    act = action or "duplicate therapy review"

                    finding = Finding(
                        rule_id=str(r_id),
                        severity=severity or "review_required",
                        title=f"Duplicate ACE-Inhibitor Therapy: {active_aces[0]} + {active_aces[1]}",
                        description=desc,
                        action=act,
                        inputs=inputs,
                        trace={
                            "rule_id": str(r_id),
                            "evidence_id": str(ev_id),
                            "source": str(source),
                            "source_version": str(source_ver),
                            "matched_pair": sorted([active_aces[0].lower(), active_aces[1].lower()]),
                        },
                        source=str(source),
                    )
                    findings.append(finding)

            elif r_type == "duplicate_therapy" and drug_a_rule:
                norm_rule_drug = normalizer.normalize(str(drug_a_rule)).lower().strip()
                matches = [
                    d for d in all_active_meds
                    if normalizer.normalize(d).lower().strip() == norm_rule_drug
                ]
                if len(matches) >= 2:
                    sig = (str(r_id), (norm_rule_drug,))
                    if sig in seen_finding_keys:
                        continue
                    seen_finding_keys.add(sig)

                    inputs = {
                        "drug_a": matches[0],
                        "drug_b": matches[1],
                        "duplicate_drug": norm_rule_drug,
                        "active_duplicates": matches,
                        "patient_id": patient_id,
                        "lab_count": len(clean_labs),
                    }
                    desc = (
                        f"{ev_text or 'Duplicate medication therapy detected.'}\n\nClinical Context: Multiple active orders/prescriptions detected "
                        f"for {norm_rule_drug} ({', '.join(matches)})."
                    )
                    act = action or "duplicate therapy review"

                    finding = Finding(
                        rule_id=str(r_id),
                        severity=severity or "review_required",
                        title=f"Duplicate Therapy: {matches[0]} + {matches[1]}",
                        description=desc,
                        action=act,
                        inputs=inputs,
                        trace={
                            "rule_id": str(r_id),
                            "evidence_id": str(ev_id),
                            "source": str(source),
                            "source_version": str(source_ver),
                            "matched_pair": [norm_rule_drug],
                        },
                        source=str(source),
                    )
                    findings.append(finding)

        if filter_unchanged and previous_findings:
            findings = self.filter_unchanged_findings(findings, previous_findings)

        return findings

    def evaluate(
        self,
        patient_context: Optional[Any] = None,
        medications: Optional[List[Any]] = None,
        labs: Optional[List[Any]] = None,
        orders: Optional[List[Any]] = None,
        previous_findings: Optional[List[Any]] = None,
        rule_context: Optional[Any] = None,
        filter_unchanged: bool = False,
    ) -> RiskAssessmentReport:
        """
        Run detection and package results into a RiskAssessmentReport.
        """
        ctx = patient_context if patient_context is not None else self.patient_context
        med_list = medications if medications is not None else self.medications
        lab_list = labs if labs is not None else self.labs

        findings = self.detect(
            patient_context=ctx,
            medications=med_list,
            labs=lab_list,
            orders=orders,
            previous_findings=previous_findings,
            rule_context=rule_context,
            filter_unchanged=filter_unchanged,
        )
        patient_id = self._extract_patient_id(ctx)

        return RiskAssessmentReport(
            patient_id=patient_id,
            findings=findings,
            evaluated_medications_count=len(med_list),
            total_findings_count=len(findings),
        )

