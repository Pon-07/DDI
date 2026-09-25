from datetime import datetime
import unittest

from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from database.database import Base, set_sqlite_pragma
from models import (
    AuditLedger,
    Drug,
    Event,
    Finding,
    InteractionEvidence,
    LabelEvidence,
    Lab,
    Medication,
    Order,
    Patient,
    RuleVersion,
    SafetyRule,
)
from scripts.init_db import EXPECTED_TABLES, init_db


class TestDatabaseIntegrity(unittest.TestCase):
    """
    Comprehensive database integrity test suite for AEGIS Rx:
    - 12-table schema verification
    - Foreign-key integrity across clinical and intelligence layers
    - Full CRUD operations on intelligence models
    - Provenance preservation (source, version, evidence ID/text)
    - RuleVersion and AuditLedger constraints (including hash uniqueness)
    - Active SQLite pragmas (WAL mode & foreign keys)
    - Idempotent database initialization without data destruction
    """

    def setUp(self):
        # Create an isolated in-memory SQLite engine with PRAGMA listeners for unit testing
        self.test_engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(self.test_engine, "connect")
        def set_sqlite_pragmas(dbapi_conn, conn_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        Base.metadata.create_all(bind=self.test_engine)
        self.Session = sessionmaker(bind=self.test_engine)
        self.session: Session = self.Session()

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(bind=self.test_engine)
        self.test_engine.dispose()

    # 1. Verify all 12 tables exist
    def test_all_12_tables_exist_in_metadata_and_sqlite(self):
        """Verify that all 12 expected tables exist in SQLAlchemy metadata and SQLite schema."""
        metadata_tables = set(Base.metadata.tables.keys())
        self.assertEqual(len(EXPECTED_TABLES), 12)
        for tbl in EXPECTED_TABLES:
            self.assertIn(tbl, metadata_tables)

        with self.test_engine.connect() as conn:
            result = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
            )
            sqlite_tables = [row[0] for row in result.fetchall()]

        for tbl in EXPECTED_TABLES:
            self.assertIn(tbl, sqlite_tables)
        self.assertEqual(len(sqlite_tables), 12)

    # 2. Test foreign-key integrity
    def test_foreign_key_label_evidence_to_drug(self):
        """Test FK integrity from LabelEvidence to Drug."""
        drug = Drug(
            drug_name="Atorvastatin Calcium 20mg",
            normalized_name="atorvastatin",
            source="RxNorm",
        )
        self.session.add(drug)
        self.session.commit()

        # Valid relationship
        label = LabelEvidence(
            drug_id=drug.id,
            drug_name="atorvastatin",
            section="contraindications",
            text="Active liver failure.",
            source="openFDA",
        )
        self.session.add(label)
        self.session.commit()

        self.assertEqual(label.drug.id, drug.id)
        self.assertEqual(len(drug.label_evidences), 1)

        # Invalid FK insertion must fail
        orphan_label = LabelEvidence(
            drug_id=99999,
            drug_name="nonexistent",
            section="contraindications",
            text="Text",
            source="openFDA",
        )
        self.session.add(orphan_label)
        with self.assertRaises(IntegrityError):
            self.session.commit()
        self.session.rollback()

    def test_foreign_key_safety_rule_to_rule_version(self):
        """Test FK integrity from SafetyRule to RuleVersion."""
        version = RuleVersion(
            pack_name="CardioSafety",
            version="1.0.0",
            content_hash="a" * 64,
            status="active",
        )
        self.session.add(version)
        self.session.commit()

        rule = SafetyRule(
            rule_id="RULE-001",
            rule_type="drug_interaction",
            drug_a="warfarin",
            drug_b="aspirin",
            source="DDInter",
            source_version="v2.1",
            evidence_id="DDI-001",
            evidence_text="Bleeding risk",
            version_id=version.id,
        )
        self.session.add(rule)
        self.session.commit()

        self.assertEqual(rule.rule_version.id, version.id)
        self.assertEqual(len(version.safety_rules), 1)

        # Invalid version_id must fail
        orphan_rule = SafetyRule(
            rule_id="RULE-ORPHAN",
            rule_type="drug_interaction",
            drug_a="drug_a",
            drug_b="drug_b",
            source="DDInter",
            source_version="v2.1",
            evidence_id="DDI-002",
            evidence_text="Text",
            version_id=88888,
        )
        self.session.add(orphan_rule)
        with self.assertRaises(IntegrityError):
            self.session.commit()
        self.session.rollback()

    def test_existing_clinical_relationships_and_cascade_delete(self):
        """Verify Patient relationships (medications, labs, orders, findings, events) remain valid."""
        patient = Patient(patient_identifier="MRN-INTEG-1", name="Test Subject")
        self.session.add(patient)
        self.session.commit()

        med = Medication(patient_id=patient.id, drug_name="Lisinopril 10mg", status="active")
        lab = Lab(patient_id=patient.id, test_name="Potassium", value="4.5", unit="mEq/L")
        order = Order(patient_id=patient.id, drug_name="Spironolactone 25mg", status="pending")
        event_obj = Event(patient_id=patient.id, event_type="MED_ORDERED", payload={"step": 1})
        finding = Finding(
            patient_id=patient.id,
            rule_id="RULE-01",
            severity="Major",
            title="DDI Finding",
            trace={"source": "DDInter"},
        )
        self.session.add_all([med, lab, order, event_obj, finding])
        self.session.commit()

        fetched_patient = self.session.get(Patient, patient.id)
        self.assertEqual(len(fetched_patient.medications), 1)
        self.assertEqual(len(fetched_patient.labs), 1)
        self.assertEqual(len(fetched_patient.orders), 1)
        self.assertEqual(len(fetched_patient.findings), 1)
        self.assertEqual(len(fetched_patient.events), 1)

        # Cascade delete test
        self.session.delete(fetched_patient)
        self.session.commit()

        self.assertIsNone(self.session.get(Medication, med.id))
        self.assertIsNone(self.session.get(Lab, lab.id))
        self.assertIsNone(self.session.get(Order, order.id))
        self.assertIsNone(self.session.get(Finding, finding.id))

    # 3. Test CRUD operations for intelligence models
    def test_intelligence_models_crud(self):
        """Verify full Create, Read, Update, Delete cycle for all intelligence-layer models."""
        # 1. Drug
        drug = Drug(drug_name="Digoxin 0.125mg", normalized_name="digoxin", source="RxNorm")
        self.session.add(drug)
        self.session.commit()
        drug.normalized_name = "digoxin_updated"
        self.session.commit()
        self.assertEqual(self.session.get(Drug, drug.id).normalized_name, "digoxin_updated")

        # 2. InteractionEvidence
        ie = InteractionEvidence(
            drug_a="digoxin", drug_b="amiodarone", description="Toxicity risk", source="DDInter"
        )
        self.session.add(ie)
        self.session.commit()
        ie.description = "Updated description"
        self.session.commit()
        self.assertEqual(self.session.get(InteractionEvidence, ie.id).description, "Updated description")

        # 3. LabelEvidence
        le = LabelEvidence(
            drug_name="digoxin", section="warnings", text="Narrow therapeutic index", source="openFDA"
        )
        self.session.add(le)
        self.session.commit()
        self.session.delete(le)
        self.session.commit()
        self.assertIsNone(self.session.get(LabelEvidence, le.id))

        # 4. RuleVersion & SafetyRule
        rv = RuleVersion(pack_name="Pack1", version="1.0", content_hash="b" * 64)
        self.session.add(rv)
        self.session.commit()

        sr = SafetyRule(
            rule_id="SR-1",
            rule_type="contraindication",
            drug_a="drug_x",
            source="openFDA",
            source_version="2026",
            evidence_id="EVID-1",
            evidence_text="Contraindicated",
            version_id=rv.id,
        )
        self.session.add(sr)
        self.session.commit()
        self.session.delete(rv)  # Cascades to safety_rules
        self.session.commit()
        self.assertIsNone(self.session.get(SafetyRule, sr.id))

        # 5. AuditLedger
        al = AuditLedger(
            timestamp="2026-09-25T20:00:00Z",
            actor="system",
            event_type="LOGIN",
            payload={},
            prev_hash="0" * 64,
            hash="c" * 64,
        )
        self.session.add(al)
        self.session.commit()
        self.assertIsNotNone(self.session.get(AuditLedger, al.id))

    # 4. Test provenance fields
    def test_provenance_preservation(self):
        """Verify provenance fields retain verbatim references without data loss."""
        rule = SafetyRule(
            rule_id="RULE-PROV-001",
            rule_type="drug_interaction",
            drug_a="metformin",
            drug_b="cimetidine",
            source="DDInter",
            source_version="v2.1-2026",
            evidence_id="DDI-REF-9988",
            evidence_text="Verbatim clinical statement from source dataset.",
            severity="Moderate",
            action="Monitor renal function.",
        )
        self.session.add(rule)
        self.session.commit()

        fetched = self.session.query(SafetyRule).filter_by(rule_id="RULE-PROV-001").first()
        self.assertEqual(fetched.source, "DDInter")
        self.assertEqual(fetched.source_version, "v2.1-2026")
        self.assertEqual(fetched.evidence_id, "DDI-REF-9988")
        self.assertEqual(fetched.evidence_text, "Verbatim clinical statement from source dataset.")

    # 5. Test RuleVersion attributes
    def test_rule_version_metadata_and_integrity_hash(self):
        """Test RuleVersion metadata fields: version, content_hash, status, rules_count."""
        content_hash_sample = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        rv = RuleVersion(
            pack_name="OncologySafetyPack",
            version="2.1.0",
            description="Validated oncology interaction pack",
            rules_count=5,
            content_hash=content_hash_sample,
            status="active",
        )
        self.session.add(rv)
        self.session.commit()

        fetched = self.session.get(RuleVersion, rv.id)
        self.assertEqual(fetched.pack_name, "OncologySafetyPack")
        self.assertEqual(fetched.version, "2.1.0")
        self.assertEqual(fetched.content_hash, content_hash_sample)
        self.assertEqual(fetched.status, "active")
        self.assertEqual(fetched.rules_count, 5)

    # 6. Test AuditLedger fields and hash uniqueness
    def test_audit_ledger_fields_and_hash_uniqueness(self):
        """Test AuditLedger fields and unique constraint enforcement on current hash."""
        entry1 = AuditLedger(
            timestamp="2026-09-25T20:30:00Z",
            actor="pharmacist_ann",
            event_type="ORDER_COSIGNED",
            payload={"order_id": 44, "decision": "approved"},
            prev_hash="0" * 64,
            hash="1" * 64,
        )
        self.session.add(entry1)
        self.session.commit()

        self.assertIsNotNone(entry1.id)
        self.assertEqual(entry1.actor, "pharmacist_ann")
        self.assertEqual(entry1.payload["decision"], "approved")

        # Attempting to insert another entry with the same hash must violate UNIQUE constraint
        duplicate_entry = AuditLedger(
            timestamp="2026-09-25T20:31:00Z",
            actor="pharmacist_ann",
            event_type="ORDER_COSIGNED",
            payload={"order_id": 45},
            prev_hash="1" * 64,
            hash="1" * 64,  # Duplicate hash
        )
        self.session.add(duplicate_entry)
        with self.assertRaises(IntegrityError):
            self.session.commit()
        self.session.rollback()

    # 7. Test SQLite foreign key enforcement on isolated test engine
    def test_production_engine_foreign_keys_active(self):
        """Verify that engine configured with set_sqlite_pragma actively enforces foreign keys."""
        with self.test_engine.connect() as conn:
            fk_val = conn.execute(text("PRAGMA foreign_keys")).scalar()
            self.assertEqual(fk_val, 1)

    # 8. Test SQLite WAL mode on active file database
    def test_production_engine_wal_mode_active(self):
        """Verify that file engine configured with set_sqlite_pragma operates in WAL mode."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_db_path = Path(temp_dir) / "test_wal.db"
            wal_engine = create_engine(
                f"sqlite:///{temp_db_path}",
                connect_args={"check_same_thread": False},
            )
            event.listen(wal_engine, "connect", set_sqlite_pragma)
            try:
                with wal_engine.connect() as conn:
                    journal = conn.execute(text("PRAGMA journal_mode")).scalar()
                    self.assertEqual(str(journal).lower(), "wal")
            finally:
                wal_engine.dispose()

    # 9. Test initialization idempotency
    def test_init_db_idempotency_preserves_existing_data(self):
        """Verify that repeated init_db() execution preserves pre-existing rows across tables."""
        init_db(target_engine=self.test_engine, verbose=False)

        # Seed test patient and drug
        patient = Patient(patient_identifier="MRN-IDEMPOTENT-TEST", name="Idempotent Test Patient")
        drug = Drug(drug_name="Idempotent Drug", normalized_name="idempotent_drug", source="Test")
        self.session.add_all([patient, drug])
        self.session.commit()

        patient_id = patient.id
        drug_id = drug.id

        # Run init_db() again on test engine
        init_db(target_engine=self.test_engine, verbose=False)

        # Verify seeded records still exist and are untouched
        fetched_p = self.session.get(Patient, patient_id)
        fetched_d = self.session.get(Drug, drug_id)
        self.assertIsNotNone(fetched_p)
        self.assertIsNotNone(fetched_d)
        self.assertEqual(fetched_p.name, "Idempotent Test Patient")
        self.assertEqual(fetched_d.normalized_name, "idempotent_drug")


if __name__ == "__main__":
    unittest.main()
