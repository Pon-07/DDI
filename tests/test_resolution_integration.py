import unittest
from datetime import datetime
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from agents.knowledge import DrugNormalizer, InteractionEvidence, KnowledgeService
from agents.resolution import (
    ResolutionCandidate,
    ResolutionPipelineResult,
    ResolutionResult,
    SafetyResolutionEngine,
    SimulatedOrder,
)
from agents.risk import Finding, RiskDetector
from agents.rules.pack_service import DEMO_RULE_PACK_PATH, RulePackService
from agents.rules.schemas import RulePack, SafetyRule as SafetyRuleSchema
from database.database import Base
from engine.event_service import MedicationEventService
from models.models import Event, Finding as DbFinding, Medication, Order, Patient, SafetyRule as DbSafetyRule


class TestSafetyResolutionIntegration(unittest.TestCase):
    """
    Isolated integration tests for SafetyResolutionEngine with RiskDetector, Finding,
    and RulePackService.
    Uses purely in-memory SQLite (never touches production aegis_rx.db).
    """

    def setUp(self):
        # Create an isolated in-memory SQLite database
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionFactory = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.SessionFactory()

        # Load demo rule pack via RulePackService into in-memory DB
        self.pack_service = RulePackService(session=self.db)
        self.rule_version = self.pack_service.load_pack(DEMO_RULE_PACK_PATH)

        # Setup KnowledgeService with known interaction
        self.normalizer = DrugNormalizer()
        self.knowledge_service = KnowledgeService(normalizer=self.normalizer)
        self.knowledge_service.add_interactions(
            [
                InteractionEvidence(
                    drug_a="warfarin",
                    drug_b="fluconazole",
                    description="Fluconazole significantly inhibits warfarin metabolism, causing sharp INR elevation.",
                    source="DDInter",
                    source_version="v2.1",
                    evidence_id="AEGIS-DEMO-EV-001",
                ),
                InteractionEvidence(
                    drug_a="lisinopril",
                    drug_b="spironolactone",
                    description="Co-administration of Lisinopril and Spironolactone may produce severe hyperkalemia.",
                    source="DDInter",
                    source_version="v2.1",
                    evidence_id="DDI-EVID-101",
                ),
            ]
        )

        # Initialize SafetyResolutionEngine loaded with active rules from RulePackService
        self.resolution_engine = SafetyResolutionEngine(rule_context=self.pack_service)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def test_end_to_end_flow_finding_to_cosign_simulated_order(self):
        """
        Test complete flow:
        Finding -> deterministic resolution -> safety-filtered candidate actions -> ranked candidates -> cosign-ready simulated order.
        """
        finding = Finding(
            rule_id="AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE",
            severity="review_required",
            title="Warfarin + Fluconazole Co-administration",
            description="AEGIS hackathon demo case: warfarin plus fluconazole with rising INR.",
            action=None,  # Not present in finding; derived from validated rule
            inputs={"drug_a": "Warfarin", "drug_b": "Fluconazole", "patient_id": 101},
            trace={
                "rule_id": "AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE",
                "evidence_id": "AEGIS-DEMO-EV-001",
                "source": "AEGIS_HACKATHON_DEMO",
                "matched_pair": ["warfarin", "fluconazole"],
            },
            source="AEGIS_HACKATHON_DEMO",
        )

        existing_med = {
            "id": 501,
            "patient_id": 101,
            "drug_name": "Warfarin Sodium 5mg",
            "dose": "5",
            "dose_unit": "mg",
            "route": "oral",
            "frequency": "daily",
        }

        # Execute full pipeline
        pipeline_result = self.resolution_engine.resolve_finding_pipeline(
            finding=finding,
            existing_medication=existing_med,
        )

        # 1. Deterministic Resolution
        self.assertIsInstance(pipeline_result, ResolutionPipelineResult)
        self.assertEqual(pipeline_result.finding_id, "AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE")
        self.assertEqual(pipeline_result.status, "actionable")
        self.assertTrue(pipeline_result.requires_cosign)
        self.assertIsNone(pipeline_result.review_reason)

        # 2. Safety-filtered Candidate Actions
        self.assertGreaterEqual(len(pipeline_result.raw_candidates), 1)
        self.assertEqual(len(pipeline_result.safety_filtered_candidates), len(pipeline_result.raw_candidates))
        candidate = pipeline_result.safety_filtered_candidates[0]
        self.assertTrue(candidate.requires_cosign)
        self.assertEqual(candidate.description, "review/hold/monitor; evaluate safer alternative")
        self.assertEqual(candidate.source, "AEGIS_HACKATHON_DEMO")
        self.assertEqual(candidate.evidence_id, "AEGIS-DEMO-EV-001")

        # 3. Ranked Candidates
        self.assertEqual(len(pipeline_result.ranked_candidates), 1)
        ranked = pipeline_result.ranked_candidates[0]
        self.assertIsNotNone(ranked.priority_score)
        self.assertGreater(ranked.priority_score, 0.0)

        # 4. Cosign-ready Simulated Order
        sim_order = pipeline_result.simulated_order
        self.assertIsNotNone(sim_order)
        self.assertIsInstance(sim_order, SimulatedOrder)
        self.assertEqual(sim_order.finding_id, "AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE")
        self.assertEqual(sim_order.patient_id, 101)
        self.assertEqual(sim_order.medication_id, 501)
        self.assertEqual(sim_order.drug_name, "Warfarin Sodium 5mg")
        self.assertEqual(sim_order.proposed_action, "review/hold/monitor; evaluate safer alternative")
        self.assertEqual(sim_order.status, "pending_cosign")
        self.assertTrue(sim_order.requires_cosign)
        self.assertFalse(sim_order.auto_execute)
        self.assertTrue(sim_order.is_simulated)
        # Check preserved doses (never invented!)
        self.assertEqual(sim_order.dose, "5")
        self.assertEqual(sim_order.dose_unit, "mg")
        self.assertEqual(sim_order.route, "oral")
        self.assertEqual(sim_order.frequency, "daily")
        self.assertEqual(sim_order.source, "AEGIS_HACKATHON_DEMO")
        self.assertEqual(sim_order.evidence_id, "AEGIS-DEMO-EV-001")

    def test_single_drug_rule_resolution_enoxaparin_renal(self):
        """
        Test single-drug rule resolution (enoxaparin renal risk demo rule).
        """
        finding = Finding(
            rule_id="AEGIS-DEMO-002-ENOXAPARIN-RENAL",
            severity="review_required",
            title="Enoxaparin with Declining Renal Function",
            description="AEGIS hackathon demo case: enoxaparin with declining renal function.",
            action=None,
            inputs={"drug_a": "enoxaparin", "patient_id": 102},
            trace={
                "rule_id": "AEGIS-DEMO-002-ENOXAPARIN-RENAL",
                "evidence_id": "AEGIS-DEMO-EV-002",
                "source": "AEGIS_HACKATHON_DEMO",
            },
            source="AEGIS_HACKATHON_DEMO",
        )

        pipeline_result = self.resolution_engine.resolve_finding_pipeline(
            finding=finding,
            patient_id=102,
        )

        self.assertEqual(pipeline_result.status, "actionable")
        self.assertEqual(len(pipeline_result.ranked_candidates), 1)
        sim_order = pipeline_result.simulated_order
        self.assertIsNotNone(sim_order)
        self.assertEqual(sim_order.drug_name, "enoxaparin")
        self.assertEqual(sim_order.proposed_action, "review/hold/monitor; evaluate dose adjustment")
        self.assertEqual(sim_order.status, "pending_cosign")
        self.assertTrue(sim_order.requires_cosign)
        self.assertFalse(sim_order.auto_execute)
        # Dose is None because no existing medication was supplied (never invented!)
        self.assertIsNone(sim_order.dose)
        self.assertIsNone(sim_order.frequency)

    def test_duplicate_therapy_rule_resolution(self):
        """
        Test duplicate therapy rule resolution (demo case 3: duplicate ACE inhibitor).
        """
        finding = Finding(
            rule_id="AEGIS-DEMO-003-DUPLICATE-ACE-INHIBITOR",
            severity="review_required",
            title="Duplicate ACE Inhibitor Therapy",
            description="AEGIS hackathon demo case: duplicate ACE-inhibitor therapy.",
            action=None,
            inputs={"drug_a": "lisinopril", "drug_b": "enalapril", "patient_id": 103},
            trace={"evidence_id": "AEGIS-DEMO-EV-003", "source": "AEGIS_HACKATHON_DEMO"},
            source="AEGIS_HACKATHON_DEMO",
        )

        pipeline_result = self.resolution_engine.resolve_finding_pipeline(finding=finding)
        self.assertEqual(pipeline_result.status, "actionable")
        self.assertEqual(
            pipeline_result.ranked_candidates[0].description,
            "duplicate therapy review",
        )
        self.assertIsNotNone(pipeline_result.simulated_order)
        self.assertEqual(pipeline_result.simulated_order.proposed_action, "duplicate therapy review")
        self.assertTrue(pipeline_result.simulated_order.requires_cosign)

    def test_unmapped_finding_requires_human_review_and_no_simulated_order(self):
        """
        Test that finding with no validated action returns requires_human_review and no simulated order.
        """
        unmapped_finding = Finding(
            rule_id="UNVALIDATED-DDI-9999",
            severity="undetermined",
            title="Unvalidated interaction",
            description="Unknown interaction without clinical guidance.",
            action=None,
            inputs={"drug_a": "DrugAlpha", "drug_b": "DrugBeta"},
            trace={"source": "Unknown"},
            source="Unknown",
        )

        pipeline_result = self.resolution_engine.resolve_finding_pipeline(unmapped_finding)

        self.assertEqual(pipeline_result.status, "requires_human_review")
        self.assertEqual(len(pipeline_result.raw_candidates), 0)
        self.assertEqual(len(pipeline_result.safety_filtered_candidates), 0)
        self.assertEqual(len(pipeline_result.ranked_candidates), 0)
        self.assertIsNone(pipeline_result.simulated_order)
        self.assertIsNotNone(pipeline_result.review_reason)
        self.assertIn("Mandatory clinical review", pipeline_result.review_reason)

    def test_safety_filtering_rejects_invalid_candidates(self):
        """
        Test safety filtering removes candidates with blank/placeholder descriptions or missing cosign.
        """
        valid_candidate = ResolutionCandidate(
            finding_id="F-1",
            action_type="drug_interaction",
            description="Hold morning dose and monitor serum potassium.",
            rationale="Evidence-based guidance",
            source="RulePack",
            evidence_id="EV-1",
            requires_cosign=True,
        )
        # Attempting candidate with description="none"
        placeholder_candidate = ResolutionCandidate(
            finding_id="F-2",
            action_type="drug_interaction",
            description="none",
            rationale="No guidance",
            source="RulePack",
            evidence_id="EV-2",
            requires_cosign=True,
        )
        # Duplicate candidate
        duplicate_candidate = ResolutionCandidate(
            finding_id="F-1",
            action_type="drug_interaction",
            description="Hold morning dose and monitor serum potassium.",
            rationale="Duplicate guidance",
            source="RulePack",
            evidence_id="EV-1",
            requires_cosign=True,
        )

        filtered = self.resolution_engine.filter_candidate_actions(
            [valid_candidate, placeholder_candidate, duplicate_candidate]
        )
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].finding_id, "F-1")
        self.assertEqual(filtered[0].description, "Hold morning dose and monitor serum potassium.")

    def test_deterministic_candidate_ranking(self):
        """
        Test deterministic candidate ranking orders critical actions ahead of monitoring actions.
        """
        avoid_candidate = ResolutionCandidate(
            finding_id="FIND-1",
            action_type="contraindication",
            description="Avoid concomitant use; discontinue immediately.",
            rationale="Critical contraindication",
            source="RulePack",
            evidence_id="EV-AVOID",
            requires_cosign=True,
        )
        monitor_candidate = ResolutionCandidate(
            finding_id="FIND-1",
            action_type="monitor",
            description="Monitor serum levels weekly.",
            rationale="Routine monitoring",
            source="RulePack",
            evidence_id="EV-MON",
            requires_cosign=True,
        )

        ranked = self.resolution_engine.rank_candidates(
            [monitor_candidate, avoid_candidate],
            finding_severity="Major",
        )

        self.assertEqual(len(ranked), 2)
        # 'avoid' action must be ranked higher than 'monitor'
        self.assertEqual(ranked[0].action_type, "contraindication")
        self.assertIn("Avoid concomitant use", ranked[0].description)
        self.assertEqual(ranked[1].action_type, "monitor")
        self.assertGreater(ranked[0].priority_score, ranked[1].priority_score)

    def test_mandatory_cosign_and_no_auto_execute_validators(self):
        """
        Test that SimulatedOrder strictly forbids auto_execute=True, requires_cosign=False, or is_simulated=False.
        """
        # Valid simulated order
        order = SimulatedOrder(
            simulation_id="SIM-1",
            finding_id="F-1",
            drug_name="Warfarin",
            proposed_action="Monitor INR",
            action_type="drug_interaction",
            rationale="Interaction evidence",
            source="DDInter",
            status="pending_cosign",
            requires_cosign=True,
            auto_execute=False,
            is_simulated=True,
        )
        self.assertTrue(order.requires_cosign)
        self.assertFalse(order.auto_execute)
        self.assertTrue(order.is_simulated)

        # Reject requires_cosign=False
        with self.assertRaises(ValidationError):
            SimulatedOrder(
                simulation_id="SIM-2",
                finding_id="F-1",
                drug_name="Warfarin",
                proposed_action="Monitor INR",
                action_type="drug_interaction",
                rationale="Interaction evidence",
                source="DDInter",
                requires_cosign=False,
            )

        # Reject auto_execute=True
        with self.assertRaises(ValidationError):
            SimulatedOrder(
                simulation_id="SIM-3",
                finding_id="F-1",
                drug_name="Warfarin",
                proposed_action="Monitor INR",
                action_type="drug_interaction",
                rationale="Interaction evidence",
                source="DDInter",
                auto_execute=True,
            )

        # Reject is_simulated=False
        with self.assertRaises(ValidationError):
            SimulatedOrder(
                simulation_id="SIM-4",
                finding_id="F-1",
                drug_name="Warfarin",
                proposed_action="Monitor INR",
                action_type="drug_interaction",
                rationale="Interaction evidence",
                source="DDInter",
                is_simulated=False,
            )

        # Reject unapproved status (e.g. 'active', 'dispensed')
        with self.assertRaises(ValidationError):
            SimulatedOrder(
                simulation_id="SIM-5",
                finding_id="F-1",
                drug_name="Warfarin",
                proposed_action="Monitor INR",
                action_type="drug_interaction",
                rationale="Interaction evidence",
                source="DDInter",
                status="active",
            )

    def test_orm_finding_compatibility_and_to_orm_order(self):
        """
        Test compatibility with SQLAlchemy ORM Finding model and converting SimulatedOrder to ORM Order.
        """
        patient = Patient(patient_identifier="MRN-ORM-TEST", name="Alice Smith")
        self.db.add(patient)
        self.db.commit()
        self.db.refresh(patient)

        orm_med = Medication(
            patient_id=patient.id,
            drug_name="Warfarin Sodium 2.5mg",
            dose="2.5",
            dose_unit="mg",
            route="oral",
            frequency="once daily",
            status="active",
        )
        self.db.add(orm_med)
        self.db.commit()
        self.db.refresh(orm_med)

        orm_finding = DbFinding(
            patient_id=patient.id,
            rule_id="AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE",
            severity="review_required",
            title="Warfarin + Fluconazole",
            description="AEGIS hackathon demo case: warfarin plus fluconazole with rising INR.",
            action=None,
            inputs={"drug_a": "Warfarin", "drug_b": "Fluconazole", "patient_id": patient.id},
            trace={
                "rule_id": "AEGIS-DEMO-001-WARFARIN-FLUCONAZOLE",
                "evidence_id": "AEGIS-DEMO-EV-001",
                "source": "AEGIS_HACKATHON_DEMO",
            },
        )
        self.db.add(orm_finding)
        self.db.commit()
        self.db.refresh(orm_finding)

        pipeline_result = self.resolution_engine.resolve_finding_pipeline(
            finding=orm_finding,
            existing_medication=orm_med,
        )

        self.assertEqual(pipeline_result.status, "actionable")
        sim_order = pipeline_result.simulated_order
        self.assertIsNotNone(sim_order)
        self.assertEqual(sim_order.dose, "2.5")
        self.assertEqual(sim_order.frequency, "once daily")

        # Convert to ORM Order
        orm_order = sim_order.to_orm_order()
        self.assertIsInstance(orm_order, Order)
        self.assertEqual(orm_order.patient_id, patient.id)
        self.assertEqual(orm_order.medication_id, orm_med.id)
        self.assertEqual(orm_order.drug_name, "Warfarin Sodium 2.5mg")
        self.assertEqual(orm_order.status, "pending_cosign")
        self.assertEqual(orm_order.dose, "2.5")

        # Add to DB session and verify it is pending
        self.db.add(orm_order)
        self.db.commit()
        self.db.refresh(orm_order)

        stored_orders = self.db.execute(select(Order).where(Order.patient_id == patient.id)).scalars().all()
        self.assertEqual(len(stored_orders), 1)
        self.assertEqual(stored_orders[0].status, "pending_cosign")

        # Confirm the patient's active medication record was NOT modified automatically
        active_med = self.db.execute(select(Medication).where(Medication.id == orm_med.id)).scalar_one()
        self.assertEqual(active_med.status, "active")
        self.assertEqual(active_med.dose, "2.5")

    def test_risk_detector_to_resolution_engine_integration(self):
        """
        Test direct pipeline integration: RiskDetector output fed directly into SafetyResolutionEngine.
        """
        # Create test rule in memory rule pack
        custom_rule = SafetyRuleSchema(
            rule_id="DDI-EVID-101",
            rule_type="drug_interaction",
            drug_a="lisinopril",
            drug_b="spironolactone",
            source="DDInter",
            source_version="v2.1",
            evidence_id="DDI-EVID-101",
            evidence_text="Co-administration of Lisinopril and Spironolactone may produce severe hyperkalemia.",
            severity="Major",
            action="Monitor serum potassium and renal function closely if concomitant use is necessary.",
            status="active",
        )
        custom_pack = RulePack(
            pack_name="LisinoprilSafety",
            version="1.0.0",
            rules=[custom_rule],
        )

        detector = RiskDetector(knowledge_service=self.knowledge_service)
        patient_ctx = {"id": 888, "name": "Bob"}
        medications = [
            {"id": 1, "drug_name": "Lisinopril 10mg", "dose": "10", "dose_unit": "mg", "status": "active"},
            {"id": 2, "drug_name": "Spironolactone 25mg", "dose": "25", "dose_unit": "mg", "status": "active"},
        ]

        # 1. RiskDetector detects interaction
        detected_findings = detector.detect(patient_context=patient_ctx, medications=medications)
        self.assertEqual(len(detected_findings), 1)
        finding = detected_findings[0]
        self.assertEqual(finding.rule_id, "DDI-EVID-101")
        self.assertIsNone(finding.action)  # Pure detection does not invent actions

        # 2. SafetyResolutionEngine resolves the finding
        engine = SafetyResolutionEngine(rule_context=custom_pack)
        pipeline_results = engine.resolve_findings_pipeline(
            findings=detected_findings,
            existing_medications=medications,
        )

        self.assertEqual(len(pipeline_results), 1)
        res = pipeline_results[0]
        self.assertEqual(res.status, "actionable")
        self.assertEqual(len(res.ranked_candidates), 1)
        self.assertEqual(
            res.ranked_candidates[0].description,
            "Monitor serum potassium and renal function closely if concomitant use is necessary.",
        )

        sim_order = res.simulated_order
        self.assertIsNotNone(sim_order)
        self.assertEqual(sim_order.patient_id, 888)
        self.assertEqual(
            sim_order.proposed_action,
            "Monitor serum potassium and renal function closely if concomitant use is necessary.",
        )
        self.assertEqual(sim_order.status, "pending_cosign")
        self.assertTrue(sim_order.requires_cosign)
        self.assertFalse(sim_order.auto_execute)

    def test_medication_event_service_with_resolutions(self):
        """
        Test MedicationEventService.process_event_with_resolutions:
        Processes event, stores findings in SQLite, and resolves into cosign-ready simulated orders.
        """
        # Register safety rule in DB for lisinopril + spironolactone
        db_rule = DbSafetyRule(
            rule_id="DDI-EVID-101",
            rule_type="drug_interaction",
            category="drug_drug_interaction",
            drug_a="lisinopril",
            drug_b="spironolactone",
            source="DDInter",
            source_version="v2.1",
            evidence_id="DDI-EVID-101",
            evidence_text="Hyperkalemia risk.",
            severity="Major",
            action="Monitor serum potassium and renal function closely if concomitant use is necessary.",
            status="active",
            version_id=self.rule_version.id,
        )
        self.db.add(db_rule)
        self.db.commit()

        # Reload rule context in engine to pick up the newly registered DB rule
        self.resolution_engine.load_rule_context(self.pack_service)

        # Patient on Lisinopril
        patient = Patient(patient_identifier="MRN-EVT-RES", name="Eve")
        self.db.add(patient)
        self.db.commit()
        self.db.refresh(patient)

        med1 = Medication(
            patient_id=patient.id,
            drug_name="Lisinopril 10mg",
            dose="10",
            dose_unit="mg",
            status="active",
        )
        self.db.add(med1)
        self.db.commit()

        event_service = MedicationEventService(
            knowledge_service=self.knowledge_service,
            resolution_engine=self.resolution_engine,
        )

        # Submit new order for Spironolactone
        db_findings, pipeline_results = event_service.process_event_with_resolutions(
            db=self.db,
            patient_id=patient.id,
            event_type="MEDICATION_ORDERED",
            payload={"drug_name": "Spironolactone 25mg", "dose": "25mg"},
            new_medication_name="Spironolactone 25mg",
        )

        # Verify DB findings created
        self.assertEqual(len(db_findings), 1)
        self.assertEqual(db_findings[0].rule_id, "DDI-EVID-101")

        # Verify pipeline results
        self.assertEqual(len(pipeline_results), 1)
        p_res = pipeline_results[0]
        self.assertEqual(p_res.status, "actionable")
        self.assertIsNotNone(p_res.simulated_order)
        self.assertEqual(p_res.simulated_order.patient_id, patient.id)
        self.assertEqual(
            p_res.simulated_order.proposed_action,
            "Monitor serum potassium and renal function closely if concomitant use is necessary.",
        )
        self.assertTrue(p_res.simulated_order.requires_cosign)
        self.assertFalse(p_res.simulated_order.auto_execute)


if __name__ == "__main__":
    unittest.main()
