from datetime import datetime
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from database.database import Base
from models import (
    AuditLedger,
    Drug,
    InteractionEvidence,
    LabelEvidence,
    RuleVersion,
    SafetyRule,
)


class TestIntelligenceModels(unittest.TestCase):
    """Unit tests for the AEGIS Rx intelligence-layer database models."""

    def setUp(self):
        # Create an isolated in-memory SQLite engine and session
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session: Session = self.Session()

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_tables_created(self):
        """Verify that all intelligence-layer tables are created in metadata."""
        table_names = set(Base.metadata.tables.keys())
        expected_tables = {
            "patients",
            "medications",
            "labs",
            "orders",
            "events",
            "findings",
            "drugs",
            "interaction_evidence",
            "label_evidence",
            "safety_rules",
            "rule_versions",
            "audit_ledger",
        }
        self.assertTrue(expected_tables.issubset(table_names))

    def test_drug_and_label_evidence_relationship(self):
        """Test Drug model CRUD, timestamps, and relationship with LabelEvidence."""
        drug = Drug(
            drug_name="Metformin Hydrochloride 500mg Oral Tablet",
            normalized_name="metformin",
            rxnorm_code="6809",
            source="RxNorm",
            source_version="2026.1",
            label_id="FDA-GLUCOPHAGE-01",
        )
        self.session.add(drug)
        self.session.commit()

        self.assertIsNotNone(drug.id)
        self.assertIsInstance(drug.created_at, datetime)
        self.assertEqual(drug.normalized_name, "metformin")

        # Add related LabelEvidence
        label1 = LabelEvidence(
            drug_id=drug.id,
            drug_name="metformin",
            section="contraindications",
            text="Severe renal impairment (eGFR < 30 mL/min).",
            source="openFDA",
            source_version="2026.1",
            evidence_id="FDA-GLUCOPHAGE-01",
        )
        label2 = LabelEvidence(
            drug_id=drug.id,
            drug_name="metformin",
            section="warnings_and_cautions",
            text="Postmarketing cases of lactic acidosis have resulted in death.",
            source="openFDA",
            source_version="2026.1",
            evidence_id="FDA-GLUCOPHAGE-01",
        )
        self.session.add_all([label1, label2])
        self.session.commit()

        # Query back from DB
        fetched_drug = self.session.get(Drug, drug.id)
        self.assertIsNotNone(fetched_drug)
        self.assertEqual(len(fetched_drug.label_evidences), 2)
        sections = {le.section for le in fetched_drug.label_evidences}
        self.assertEqual(sections, {"contraindications", "warnings_and_cautions"})
        self.assertEqual(fetched_drug.label_evidences[0].drug.id, drug.id)

    def test_interaction_evidence_model(self):
        """Test InteractionEvidence model CRUD and provenance retention."""
        evidence = InteractionEvidence(
            drug_a="lisinopril",
            drug_b="spironolactone",
            description="Concomitant use may result in severe hyperkalemia.",
            source="DDInter",
            source_version="v2.0",
            evidence_id="DDI-7001_DDI-7002",
        )
        self.session.add(evidence)
        self.session.commit()

        self.assertIsNotNone(evidence.id)
        self.assertEqual(evidence.source, "DDInter")
        self.assertEqual(evidence.evidence_id, "DDI-7001_DDI-7002")

        fetched = self.session.query(InteractionEvidence).filter_by(drug_a="lisinopril").first()
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.drug_b, "spironolactone")
        self.assertIn("hyperkalemia", fetched.description)

    def test_rule_version_and_safety_rules_relationship(self):
        """Test RuleVersion and SafetyRule models and their hierarchical relationship."""
        version = RuleVersion(
            pack_name="CardioRenalSafetyPack",
            version="1.0.0",
            description="Cardiovascular and renal validated safety rules",
            rules_count=2,
            content_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            status="active",
        )
        self.session.add(version)
        self.session.commit()

        rule1 = SafetyRule(
            rule_id="RULE-CR-001",
            rule_type="drug_interaction",
            category="electrolytes",
            drug_a="lisinopril",
            drug_b="spironolactone",
            source="DDInter",
            source_version="v2.0",
            evidence_id="DDI-7001",
            evidence_text="Risk of hyperkalemia.",
            severity="Major",
            action="Monitor potassium within 48-72h.",
            status="active",
            version_id=version.id,
        )
        rule2 = SafetyRule(
            rule_id="RULE-CR-002",
            rule_type="contraindication",
            category="renal",
            drug_a="metformin",
            drug_b=None,
            source="openFDA",
            source_version="2026.1",
            evidence_id="FDA-001",
            evidence_text="Contraindicated in severe renal impairment.",
            severity="High",
            action="Withhold metformin in acute kidney injury.",
            status="active",
            version_id=version.id,
        )
        self.session.add_all([rule1, rule2])
        self.session.commit()

        # Query version and verify cascade and relationships
        fetched_version = self.session.get(RuleVersion, version.id)
        self.assertIsNotNone(fetched_version)
        self.assertEqual(len(fetched_version.safety_rules), 2)
        rule_ids = {r.rule_id for r in fetched_version.safety_rules}
        self.assertEqual(rule_ids, {"RULE-CR-001", "RULE-CR-002"})

        # Verify back-population
        self.assertEqual(fetched_version.safety_rules[0].rule_version.pack_name, "CardioRenalSafetyPack")

    def test_audit_ledger_model(self):
        """Test AuditLedger ORM model schema matches cryptographic audit records."""
        entry = AuditLedger(
            timestamp="2026-09-25T19:00:00.000000Z",
            actor="dr_house",
            event_type="MEDICATION_PRESCRIBED",
            payload={"patient_id": 105, "drug_name": "Warfarin", "dose": "5mg"},
            prev_hash="0" * 64,
            hash="a" * 64,
        )
        self.session.add(entry)
        self.session.commit()

        self.assertIsNotNone(entry.id)
        fetched = self.session.query(AuditLedger).filter_by(actor="dr_house").first()
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.event_type, "MEDICATION_PRESCRIBED")
        self.assertEqual(fetched.payload["drug_name"], "Warfarin")
        self.assertEqual(fetched.prev_hash, "0" * 64)
        self.assertEqual(fetched.hash, "a" * 64)


if __name__ == "__main__":
    unittest.main()
