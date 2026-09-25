import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from agents.rules.pack_service import (
    DEMO_RULE_PACK_PATH,
    RulePackService,
    RulePackValidationError,
    RulePackVersionExistsError,
    compute_content_hash,
    load_rule_pack_file,
)
from database.database import Base
from models import RuleVersion, SafetyRule


def _production_db_snapshot():
    db_path = Path("aegis_rx.db")
    if not db_path.exists():
        return None
    stat = db_path.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _valid_rule(rule_id: str, **overrides):
    rule = {
        "rule_id": rule_id,
        "rule_type": "drug_interaction",
        "category": "drug_drug_interaction",
        "drug_a": "warfarin",
        "drug_b": "fluconazole",
        "source": "AEGIS_HACKATHON_DEMO",
        "source_version": "1.0.0",
        "evidence_id": f"EV-{rule_id}",
        "evidence_text": "Qualitative interaction review. Numeric INR threshold requires validated evidence.",
        "severity": "review_required",
        "action": "review/hold/monitor; evaluate safer alternative",
        "status": "active",
    }
    rule.update(overrides)
    return rule


class TestRulePackService(unittest.TestCase):
    """Isolated tests for deterministic SafetyRule pack persistence."""

    def setUp(self):
        self.prod_snapshot = _production_db_snapshot()
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session: Session = self.Session()
        self.service = RulePackService(self.session)

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()
        self.assertEqual(_production_db_snapshot(), self.prod_snapshot)

    def _create_pack(self, rules, **pack_overrides):
        payload = {
            "pack_name": "test_pack",
            "version": "1.0.0",
            "description": "Test rule pack",
            "rules": rules,
        }
        payload.update(pack_overrides)
        return self.service.create_pack(payload)

    def test_valid_rule_pack_creation(self):
        version = self._create_pack(
            [_valid_rule("PACK-A"), _valid_rule("PACK-B", drug_a="enoxaparin", drug_b=None)]
        )
        self.assertIsNotNone(version.id)
        self.assertEqual(version.pack_name, "test_pack")
        self.assertEqual(version.version, "1.0.0")
        self.assertEqual(version.description, "Test rule pack")
        self.assertEqual(version.rules_count, 2)
        self.assertEqual(version.status, "active")
        self.assertEqual(len(version.content_hash), 64)

    def test_rule_version_record_created(self):
        self._create_pack([_valid_rule("PACK-RV-1")])
        stored = self.session.query(RuleVersion).filter_by(pack_name="test_pack").one()
        self.assertEqual(stored.version, "1.0.0")
        self.assertEqual(stored.rules_count, 1)
        self.assertTrue(stored.content_hash)

    def test_safety_rules_created_and_linked(self):
        version = self._create_pack([_valid_rule("PACK-LINK-1"), _valid_rule("PACK-LINK-2")])
        rules = self.session.query(SafetyRule).order_by(SafetyRule.rule_id).all()
        self.assertEqual(len(rules), 2)
        self.assertEqual({r.version_id for r in rules}, {version.id})
        self.assertEqual(len(version.safety_rules), 2)

    def test_deterministic_hash_generation(self):
        rules = [_valid_rule("PACK-H-1"), _valid_rule("PACK-H-2")]
        expected = compute_content_hash(rules)
        version = self._create_pack(rules)
        self.assertEqual(version.content_hash, expected)

    def test_same_rules_same_hash(self):
        rules_a = [
            _valid_rule("PACK-S-2"),
            _valid_rule("PACK-S-1"),
        ]
        rules_b = [
            {
                "action": "review/hold/monitor; evaluate safer alternative",
                "status": "active",
                "severity": "review_required",
                "evidence_text": "Qualitative interaction review. Numeric INR threshold requires validated evidence.",
                "evidence_id": "EV-PACK-S-1",
                "source_version": "1.0.0",
                "source": "AEGIS_HACKATHON_DEMO",
                "drug_b": "fluconazole",
                "drug_a": "warfarin",
                "category": "drug_drug_interaction",
                "rule_type": "drug_interaction",
                "rule_id": "PACK-S-1",
            },
            _valid_rule("PACK-S-2"),
        ]
        self.assertEqual(compute_content_hash(rules_a), compute_content_hash(rules_b))
        version = self._create_pack(rules_b, pack_name="hash_a")
        self.assertEqual(version.content_hash, compute_content_hash(rules_a))

    def test_changed_rule_content_changes_hash(self):
        original = [_valid_rule("PACK-C-1")]
        changed = [_valid_rule("PACK-C-1", evidence_text="Changed evidence text.")]
        self.assertNotEqual(compute_content_hash(original), compute_content_hash(changed))
        version = self._create_pack(original, pack_name="changed_a")
        self.assertEqual(version.content_hash, compute_content_hash(original))
        self.assertNotEqual(version.content_hash, compute_content_hash(changed))

    def test_duplicate_rule_id_rejected(self):
        with self.assertRaises(RulePackValidationError) as ctx:
            self._create_pack([_valid_rule("PACK-DUP"), _valid_rule("PACK-DUP")])
        self.assertTrue(
            any("Duplicate rule_id" in issue.message for issue in ctx.exception.issues)
        )
        self.assertEqual(self.session.query(RuleVersion).count(), 0)
        self.assertEqual(self.session.query(SafetyRule).count(), 0)

    def test_missing_provenance_rejected(self):
        required = [
            "rule_id",
            "rule_type",
            "category",
            "source",
            "source_version",
            "evidence_id",
            "evidence_text",
            "severity",
            "action",
        ]
        for field in required:
            rule = _valid_rule(f"PACK-MISS-{field}")
            rule[field] = ""
            with self.assertRaises(RulePackValidationError) as ctx:
                self._create_pack([rule], version=f"miss-{field}")
            fields = [issue.field for issue in ctx.exception.issues]
            self.assertIn(field, fields)
        self.assertEqual(self.session.query(SafetyRule).count(), 0)

    def test_malformed_rule_rejected(self):
        with self.assertRaises(RulePackValidationError):
            self._create_pack(["not-a-rule"])
        with self.assertRaises(RulePackValidationError):
            self._create_pack([42])
        with self.assertRaises(RulePackValidationError):
            self.service.create_pack(
                {
                    "pack_name": "bad",
                    "version": "1.0.0",
                    "rules": {"rule_id": "x"},
                }
            )
        self.assertEqual(self.session.query(RuleVersion).count(), 0)

    def test_existing_version_is_not_overwritten(self):
        self._create_pack([_valid_rule("PACK-OW-1")])
        with self.assertRaises(RulePackVersionExistsError):
            self._create_pack([_valid_rule("PACK-OW-2")])
        version = self.session.query(RuleVersion).one()
        self.assertEqual(version.rules_count, 1)
        self.assertEqual(self.session.query(SafetyRule).count(), 1)
        self.assertEqual(self.session.query(SafetyRule).one().rule_id, "PACK-OW-1")

    def test_active_rule_retrieval(self):
        self._create_pack(
            [_valid_rule("PACK-ACT-1"), _valid_rule("PACK-ACT-2", status="draft")],
            pack_name="active_pack",
        )
        self._create_pack(
            [_valid_rule("PACK-INACT-1")],
            pack_name="inactive_pack",
            status="archived",
        )
        active = self.service.get_active_rules()
        self.assertEqual([rule.rule_id for rule in active], ["PACK-ACT-1"])
        pack_active = self.service.get_active_rules(pack_name="active_pack")
        self.assertEqual([rule.rule_id for rule in pack_active], ["PACK-ACT-1"])

    def test_stored_hash_verification(self):
        version = self._create_pack([_valid_rule("PACK-VER-1")])
        result = self.service.verify_content_hash(version.id)
        self.assertTrue(result.matches)
        self.assertEqual(result.stored_hash, result.computed_hash)

    def test_tampered_rule_detected(self):
        version = self._create_pack([_valid_rule("PACK-TAMPER-1")])
        rule = self.session.query(SafetyRule).filter_by(rule_id="PACK-TAMPER-1").one()
        rule.evidence_text = "tampered evidence"
        self.session.commit()
        result = self.service.verify_content_hash(version.id)
        self.assertFalse(result.matches)
        self.assertNotEqual(result.stored_hash, result.computed_hash)

    def test_demo_rules_load_successfully(self):
        pack = load_rule_pack_file(DEMO_RULE_PACK_PATH)
        version = self.service.create_pack(pack)
        rules = self.session.query(SafetyRule).order_by(SafetyRule.rule_id).all()
        self.assertEqual(version.pack_name, "aegis_hackathon_demo")
        self.assertEqual(version.version, "1.0.0")
        self.assertEqual(len(rules), 3)
        self.assertEqual(version.rules_count, 3)
        expected_ids = {
            "AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE",
            "AEGIS-DEMO-002-ENOXAPARIN-RENAL",
            "AEGIS-DEMO-003-DUPLICATE-ACE-INHIBITOR",
        }
        self.assertEqual({rule.rule_id for rule in rules}, expected_ids)
        self.assertEqual({rule.version_id for rule in rules}, {version.id})

    def test_demo_rule_count(self):
        version = self.service.load_pack(DEMO_RULE_PACK_PATH)
        self.assertEqual(version.rules_count, 3)
        self.assertEqual(self.session.query(SafetyRule).count(), 3)

    def test_demo_rules_contain_provenance(self):
        self.service.load_pack(DEMO_RULE_PACK_PATH)
        rules = self.session.query(SafetyRule).all()
        self.assertEqual(len(rules), 3)
        for rule in rules:
            self.assertTrue(rule.source)
            self.assertTrue(rule.source_version)
            self.assertTrue(rule.evidence_id)
            self.assertTrue(rule.evidence_text)
            self.assertTrue(rule.severity)
            self.assertTrue(rule.action)
            self.assertTrue(rule.category)
            self.assertTrue(rule.rule_type)

    def test_tests_do_not_write_production_db(self):
        before = _production_db_snapshot()
        self.service.load_pack(DEMO_RULE_PACK_PATH)
        self._create_pack([_valid_rule("PACK-PROD-GUARD")], pack_name="prod_guard")
        self.assertEqual(_production_db_snapshot(), before)


if __name__ == "__main__":
    unittest.main()
