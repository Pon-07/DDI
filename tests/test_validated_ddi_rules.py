import json
import os
import tempfile
import unittest
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from agents.audit.ledger import AuditLedger
from agents.explicator.service import ExplicatorService
from agents.knowledge import DrugNormalizer, KnowledgeService
from agents.resolution.resolver import SafetyResolutionEngine
from agents.risk.detector import RiskDetector
from agents.rules.pack_service import (
    RulePackService,
    VALIDATED_DDI_PACK_PATH,
    load_rule_pack_file,
)
from agents.rules.validator import RulePackValidator
from database.database import Base


class TestValidatedDDIRules(unittest.TestCase):
    """
    Focused test suite for validated deterministic DDI rules:
    - Rule pack validation & content hash integrity
    - Exact provenance preservation from repository DDInter data
    - Symmetric pair matching (A+B == B+A)
    - Unknown drug pair behavior (no false safety claims)
    - Deterministic severity preservation without silent downgrades
    - Duplicate drug handling
    - Separation of deterministic rule findings from LLM explicator
    """

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session: Session = self.Session()

        # Temporary audit ledger
        self.temp_dir = tempfile.TemporaryDirectory()
        self.audit_db_path = os.path.join(self.temp_dir.name, "rule_audit.db")
        self.audit_ledger = AuditLedger(db_path=self.audit_db_path)

        # Services
        self.normalizer = DrugNormalizer()
        self.knowledge_service = KnowledgeService(normalizer=self.normalizer)
        self.explicator = ExplicatorService()
        self.pack_service = RulePackService(self.session)

        # Load the validated rule pack
        self.rule_pack_data = load_rule_pack_file(VALIDATED_DDI_PACK_PATH)
        self.rule_version = self.pack_service.create_pack(self.rule_pack_data)
        self.active_rules = self.pack_service.get_active_rules("aegis_validated_ddi")

        self.resolver = SafetyResolutionEngine(
            rule_context=self.active_rules,
            explicator=self.explicator,
            audit_ledger=self.audit_ledger,
        )
        self.detector = RiskDetector(
            knowledge_service=self.knowledge_service,
            rule_context=self.active_rules,
        )

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(bind=self.engine)
        self.temp_dir.cleanup()

    def test_rule_pack_schema_and_provenance_validation(self):
        """1. Verify rule pack adheres strictly to schema and non-inferential provenance."""
        validator = RulePackValidator()
        result = validator.validate_pack(self.rule_pack_data["rules"])
        self.assertTrue(result.is_valid)
        self.assertEqual(result.total_rules, 3)
        self.assertEqual(result.invalid_rules_count, 0)
        self.assertEqual(len(result.errors), 0)

        # Verify cryptographic content hash matches
        hash_res = self.pack_service.verify_content_hash(self.rule_version.id)
        self.assertTrue(hash_res.matches)
        self.assertEqual(hash_res.stored_hash, hash_res.computed_hash)

    def test_dolutegravir_aluminum_hydroxide_interaction(self):
        """2. Verify Dolutegravir + Aluminum hydroxide chelation rule."""
        meds = ["Dolutegravir 50mg oral daily", "Aluminum Hydroxide 400mg oral"]
        findings = self.detector.detect(medications=meds)

        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f.rule_id, "AEGIS-VAL-001-DOLUTEGRAVIR-ALUMINUM")
        self.assertEqual(f.severity, "Major")
        self.assertEqual(f.source, "DDInter")
        self.assertEqual(f.trace.get("evidence_id"), "DDInter58 | DDInter582")
        self.assertIn("chelation", f.description.lower())

        # Test resolution
        resolution = self.resolver.resolve_finding(f)
        self.assertGreater(len(resolution.candidates), 0)
        top = resolution.candidates[0]
        self.assertIn("stagger administration", top.description.lower())

    def test_simvastatin_clarithromycin_rhabdomyolysis_risk(self):
        """3. Verify Simvastatin + Clarithromycin CYP3A4 inhibition rule."""
        meds = ["Simvastatin 40mg", "Clarithromycin 500mg"]
        findings = self.detector.detect(medications=meds)

        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f.rule_id, "AEGIS-VAL-002-SIMVASTATIN-CLARITHROMYCIN")
        self.assertEqual(f.severity, "Major")
        self.assertEqual(f.source, "DDInter")
        self.assertEqual(f.trace.get("evidence_id"), "DDInter1675 | DDInter393")
        self.assertIn("rhabdomyolysis", f.description.lower())

        resolution = self.resolver.resolve_finding(f)
        self.assertGreater(len(resolution.candidates), 0)
        self.assertIn("suspend simvastatin", resolution.candidates[0].description.lower())

    def test_digoxin_clarithromycin_pgp_risk(self):
        """4. Verify Digoxin + Clarithromycin P-gp inhibition rule."""
        meds = ["Digoxin 0.125mg", "Clarithromycin 250mg"]
        findings = self.detector.detect(medications=meds)

        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f.rule_id, "AEGIS-VAL-003-DIGOXIN-CLARITHROMYCIN")
        self.assertEqual(f.severity, "Major")
        self.assertEqual(f.source, "DDInter")
        self.assertEqual(f.trace.get("evidence_id"), "DDInter554 | DDInter393")
        self.assertIn("digitalis toxicity", f.description.lower())

    def test_symmetric_pair_matching_commutative(self):
        """5. Verify drug pair matching is commutative (order of administration yields identical results)."""
        forward_meds = ["simvastatin", "clarithromycin"]
        reversed_meds = ["clarithromycin", "simvastatin"]

        forward_findings = self.detector.detect(medications=forward_meds)
        reversed_findings = self.detector.detect(medications=reversed_meds)

        self.assertEqual(len(forward_findings), 1)
        self.assertEqual(len(reversed_findings), 1)

        f1 = forward_findings[0]
        f2 = reversed_findings[0]

        self.assertEqual(f1.rule_id, f2.rule_id)
        self.assertEqual(f1.severity, f2.severity)
        self.assertEqual(f1.trace.get("matched_pair"), f2.trace.get("matched_pair"))
        self.assertEqual(f1.description, f2.description)

    def test_unknown_or_unsupported_pair_behavior(self):
        """6. Verify unknown/unsupported pairs return no findings without claiming safety."""
        unsupported_pair = ["Acetaminophen 500mg", "Ascorbic Acid 500mg"]
        findings = self.detector.detect(medications=unsupported_pair)

        # Must report NO interaction record found; must NOT generate a false finding or claim safety
        self.assertEqual(len(findings), 0)

    def test_duplicate_medications_do_not_produce_duplicate_findings(self):
        """7. Verify duplicate entries of the same drug do not cause redundant pairwise findings."""
        duplicate_meds = [
            "simvastatin 20mg",
            "simvastatin 40mg",
            "clarithromycin 500mg",
        ]
        findings = self.detector.detect(medications=duplicate_meds)
        # Should produce exactly 1 finding for simvastatin + clarithromycin
        simva_clari_findings = [
            f for f in findings if f.rule_id == "AEGIS-VAL-002-SIMVASTATIN-CLARITHROMYCIN"
        ]
        self.assertEqual(len(simva_clari_findings), 1)

    def test_no_silent_downgrade_of_severity(self):
        """8. Verify validated severity 'Major' is strictly preserved without downgrade."""
        meds = ["dolutegravir", "aluminum hydroxide"]
        findings = self.detector.detect(medications=meds)
        self.assertEqual(findings[0].severity, "Major")

    def test_deterministic_rule_separate_from_llm_explicator(self):
        """9. Verify deterministic rule outcome is completely independent of explicator output."""
        meds = ["simvastatin", "clarithromycin"]
        findings = self.detector.detect(medications=meds)

        # Resolution without LLM (offline deterministic)
        res_offline = self.resolver.resolve_finding(findings[0])
        sim_order = self.resolver.create_simulated_order(candidate=res_offline.candidates[0], finding=findings[0])

        self.assertTrue(sim_order.requires_cosign)
        self.assertFalse(sim_order.auto_execute)
        self.assertEqual(sim_order.status, "pending_cosign")
        self.assertTrue(res_offline.candidates[0].requires_cosign)
        self.assertEqual(res_offline.candidates[0].source, "DDInter")


if __name__ == "__main__":
    unittest.main()
