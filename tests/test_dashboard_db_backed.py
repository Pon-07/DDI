import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agents.audit.ledger import AuditLedger
from app.routers import dashboards
from app.routers.dashboards import (
    get_admin_dashboard_data,
    get_nurse_dashboard_data,
    get_pharmacist_dashboard_data,
    get_patient_medical_history_endpoint,
    list_patient_medical_histories,
    NurseMARActionRequest,
    NurseRecordVitalRequest,
    record_nurse_mar_action,
    record_nurse_vital,
)
from database.database import Base
from engine.auth.models import AuthBase, User
from models import Event, Finding, Lab, Medication, Order, Patient, SubstitutionRequest


def test_dashboard_and_history_read_persisted_rows_only(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    AuthBase.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    patient = Patient(
        patient_identifier="SYN-PT-1",
        name="Synthetic Patient",
        sex="X",
        allergy_information="Recorded synthetic allergy",
    )
    session.add(patient)
    session.flush()
    medication = Medication(
        patient_id=patient.id,
        drug_name="Synthetic Drug",
        dose="5",
        dose_unit="mg",
        route="oral",
        frequency="daily",
        status="active",
    )
    lab = Lab(
        patient_id=patient.id,
        test_name="Synthetic Test",
        value="7",
        unit="units",
        reference_range="1-9",
    )
    session.add_all([medication, lab])
    session.flush()
    order = Order(
        patient_id=patient.id,
        medication_id=medication.id,
        drug_name="Synthetic Drug",
        dose="5",
        dose_unit="mg",
        status="pending",
    )
    finding = Finding(
        patient_id=patient.id,
        rule_id="SYNTHETIC_RULE",
        severity="moderate",
        title="Synthetic finding",
        description="Synthetic-only test record",
        action="Review",
    )
    event = Event(patient_id=patient.id, event_type="SYNTHETIC_EVENT", payload={"source": "test"})
    session.add_all([order, finding, event])
    session.flush()
    session.add(SubstitutionRequest(
        patient_id=patient.id,
        order_id=order.id,
        medication_id=medication.id,
        idempotency_key="synthetic-request-key-0001",
        suggested_brand="Synthetic Brand",
        reason="Synthetic test reason",
        requested_by_user_id=1,
        requested_by_name="Synthetic Pharmacist",
        status="pending",
    ))
    session.commit()

    nurse_user = User(id=1, full_name="Synthetic Nurse", role="Nurse", phone_number="synthetic")
    nurse_data = get_nurse_dashboard_data(db=session, current_user=nurse_user)
    assert [row["medication"] for row in nurse_data["mar_schedule"]] == ["Synthetic Drug"]
    assert nurse_data["mar_schedule"][0]["status"] == "NOT_RECORDED"
    assert nurse_data["mar_schedule_available"] is False
    assert [row["parameter"] for row in nurse_data["vitals_labs"]] == ["Synthetic Test"]

    pharmacist_user = User(id=1, full_name="Synthetic Pharmacist", role="Clinical Pharmacist", phone_number="synthetic")
    pharmacist_data = get_pharmacist_dashboard_data(db=session, current_user=pharmacist_user)
    assert len(pharmacist_data["availability_queue"]) == 1
    assert pharmacist_data["availability_queue"][0]["patient_identifier"] == "SYN-PT-1"
    assert pharmacist_data["availability_queue"][0]["availability"] == "UNTRACKED"
    assert pharmacist_data["local_inventory"] == []
    assert pharmacist_data["stats"]["doctor_approval_pending"] == 1

    doctor_user = User(id=1, full_name="Synthetic Doctor", role="Doctor", phone_number="synthetic")
    history_data = get_patient_medical_history_endpoint(identifier="SYN-PT-1", db=session, current_user=doctor_user)
    assert history_data["patient_name"] == "Synthetic Patient"
    assert history_data["blood_group"] is None
    assert history_data["conditions_summary"] == "Not recorded"
    assert history_data["medication_history"][0]["medication"] == "Synthetic Drug"
    assert history_data["laboratory_history"][0]["result"] == "7"
    assert history_data["clinical_assessments"][0]["assessment"] == "Synthetic finding"
    assert [row["patient_identifier"] for row in list_patient_medical_histories(db=session, current_user=doctor_user)] == ["SYN-PT-1"]

    audit_ledger = AuditLedger(db_path=tmp_path / "synthetic-audit.db")
    mar_result = record_nurse_mar_action(
        req=NurseMARActionRequest(mar_id=f"MED-{medication.id}", action="ADMINISTERED"),
        db=session,
        audit_ledger=audit_ledger,
        current_user=nurse_user,
    )
    assert mar_result["status"] == "ADMINISTERED"
    assert get_nurse_dashboard_data(db=session, current_user=nurse_user)["mar_schedule"][0]["status"] == "ADMINISTERED"
    with pytest.raises(HTTPException) as duplicate:
        record_nurse_mar_action(
            req=NurseMARActionRequest(mar_id=f"MED-{medication.id}", action="ADMINISTERED"),
            db=session,
            audit_ledger=audit_ledger,
            current_user=nurse_user,
        )
    assert duplicate.value.status_code == 409

    vital_result = record_nurse_vital(
        req=NurseRecordVitalRequest(case_id="SYN-PT-1", parameter="Synthetic Vital", value="12", unit="units"),
        db=session,
        audit_ledger=audit_ledger,
        current_user=nurse_user,
    )
    assert vital_result["status"] == "RECORDED"
    assert session.query(Lab).filter_by(test_name="Synthetic Vital").count() == 1

    class SyntheticProvider:
        def get_provider_name(self):
            return "synthetic"

    monkeypatch.setattr(dashboards, "get_sms_provider", lambda: SyntheticProvider())
    monkeypatch.setattr(dashboards, "list_available_providers", lambda: ["synthetic"])
    admin_user = User(id=2, full_name="Synthetic Administrator", role="Administrator", phone_number="synthetic-admin")
    admin_data = get_admin_dashboard_data(db=session, audit_ledger=audit_ledger, current_user=admin_user)
    assert admin_data["patients"] == [{
        "patient_identifier": "SYN-PT-1",
        "name": "Synthetic Patient",
        "date_of_birth": None,
        "sex": "X",
        "department": None,
        "latest_finding": "Synthetic finding",
        "active_medications": ["Synthetic Drug"],
        "risk": "MODERATE",
    }]
    assert admin_data["audit_chain"]["total_records"] == len(admin_data["audit_chain"]["recent_records"])
    assert any(row["event_type"] == "VITAL_SIGN_RECORDED" for row in admin_data["audit_chain"]["recent_records"])
    assert all(row["is_chain_valid"] for row in admin_data["audit_chain"]["recent_records"])

    session.close()
    AuthBase.metadata.drop_all(bind=engine)
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
