import unittest
from datetime import datetime

from agents.rules import (
    RulePack,
    RulePackValidationResult,
    RulePackValidator,
    SafetyRule,
)


class TestRulePack(unittest.TestCase):
    """Unit tests for the validated safety rule schema and RulePackValidator."""

    def setUp(self):
        self.validator = RulePackValidator()

    def test_valid_rule_instantiation(self):
        rule = SafetyRule(
            rule_id="RULE-DDI-001",
            rule_type="drug_interaction",
            drug_a="lisinopril",
            drug_b="spironolactone",
            source="DDInter",
            source_version="v2.1",
            evidence_id="DDI-9001",
            evidence_text="Co-administration of ACE inhibitors and potassium-sparing diuretics may cause severe hyperkalemia.",
            severity=None,  # Unspecified
            action=None,  # Unspecified
            status="active",
        )
        is_valid, validated_rule, issues = self.validator.validate_rule(rule)
        self.assertTrue(is_valid)
        self.assertIsNotNone(validated_rule)
        self.assertEqual(len(issues), 0)
        self.assertEqual(validated_rule.severity, None)
        self.assertEqual(validated_rule.action, None)
        self.assertEqual(validated_rule.evidence_id, "DDI-9001")

    def test_valid_rule_from_dict(self):
        rule_dict = {
            "rule_id": "RULE-CI-002",
            "rule_type": "contraindication",
            "drug_a": "metformin",
            "source": "openFDA",
            "source_version": "2026.1",
            "evidence_id": "FDA-LBL-001",
            "evidence_text": "Contraindicated in severe renal impairment.",
            "status": "active",
        }
        is_valid, validated_rule, issues = self.validator.validate_rule(rule_dict)
        self.assertTrue(is_valid)
        self.assertIsNotNone(validated_rule)
        self.assertEqual(validated_rule.drug_a, "metformin")
        self.assertEqual(validated_rule.drug_b, None)

    def test_invalid_rule_missing_evidence_id(self):
        rule_dict = {
            "rule_id": "RULE-BAD-01",
            "rule_type": "drug_interaction",
            "drug_a": "warfarin",
            "source": "DDInter",
            "source_version": "v2.1",
            "evidence_id": "",  # Empty
            "evidence_text": "Bleeding risk.",
            "status": "active",
        }
        is_valid, validated_rule, issues = self.validator.validate_rule(rule_dict)
        self.assertFalse(is_valid)
        self.assertIsNone(validated_rule)
        fields = [iss.field for iss in issues]
        self.assertIn("evidence_id", fields)

    def test_invalid_rule_missing_source_provenance(self):
        rule_dict = {
            "rule_id": "RULE-BAD-02",
            "rule_type": "drug_interaction",
            "drug_a": "warfarin",
            # missing source and source_version
            "evidence_id": "EV-01",
            "evidence_text": "Bleeding risk.",
            "status": "active",
        }
        is_valid, validated_rule, issues = self.validator.validate_rule(rule_dict)
        self.assertFalse(is_valid)
        fields = [iss.field for iss in issues]
        self.assertTrue(any("source" in f for f in fields))

    def test_invalid_rule_status(self):
        rule_dict = {
            "rule_id": "RULE-BAD-03",
            "rule_type": "drug_interaction",
            "drug_a": "lisinopril",
            "source": "DDInter",
            "source_version": "v2.1",
            "evidence_id": "EV-02",
            "evidence_text": "Hypotension risk.",
            "status": "invalid_status_xyz",
        }
        is_valid, validated_rule, issues = self.validator.validate_rule(rule_dict)
        self.assertFalse(is_valid)
        fields = [iss.field for iss in issues]
        self.assertIn("status", fields)

    def test_validate_rule_pack_success(self):
        rules = [
            SafetyRule(
                rule_id="PACK-001",
                rule_type="drug_interaction",
                drug_a="lisinopril",
                drug_b="spironolactone",
                source="DDInter",
                source_version="v2.1",
                evidence_id="EV-101",
                evidence_text="Hyperkalemia risk.",
                status="active",
            ),
            SafetyRule(
                rule_id="PACK-002",
                rule_type="contraindication",
                drug_a="atorvastatin",
                source="DailyMed",
                source_version="2026.01",
                evidence_id="SPL-202",
                evidence_text="Active liver disease.",
                status="active",
            ),
        ]
        result = self.validator.validate_pack(rules)
        self.assertTrue(result.is_valid)
        self.assertEqual(result.total_rules, 2)
        self.assertEqual(result.valid_rules_count, 2)
        self.assertEqual(result.invalid_rules_count, 0)
        self.assertEqual(len(result.errors), 0)

    def test_validate_rule_pack_duplicate_rule_id(self):
        rules = [
            SafetyRule(
                rule_id="PACK-DUP-01",
                rule_type="drug_interaction",
                drug_a="lisinopril",
                drug_b="spironolactone",
                source="DDInter",
                source_version="v2.1",
                evidence_id="EV-101",
                evidence_text="Hyperkalemia risk.",
                status="active",
            ),
            SafetyRule(
                rule_id="PACK-DUP-01",  # Duplicate rule_id
                rule_type="drug_interaction",
                drug_a="losartan",
                drug_b="spironolactone",
                source="DDInter",
                source_version="v2.1",
                evidence_id="EV-102",
                evidence_text="Hyperkalemia risk.",
                status="active",
            ),
        ]
        result = self.validator.validate_pack(rules)
        self.assertFalse(result.is_valid)
        self.assertEqual(result.total_rules, 2)
        self.assertEqual(result.valid_rules_count, 1)
        self.assertEqual(result.invalid_rules_count, 1)
        self.assertTrue(any("Duplicate rule_id" in err.message for err in result.errors))


if __name__ == "__main__":
    unittest.main()
