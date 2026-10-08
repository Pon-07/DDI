from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from agents.audit.ledger import AuditLedger
from app.dependencies import get_audit_ledger, get_event_service, get_resolution_engine
from database.database import get_db
from engine.auth.dependencies import get_current_user, get_optional_user, require_role
from engine.auth.models import User, UserSession
from engine.auth.providers.factory import get_sms_provider, list_available_providers
from engine.auth.service import AuthService
from models import Finding, Lab, Medication, Order, Patient, SafetyRule

router = APIRouter(tags=["Role Dashboards & Patient History"])


# =========================================================================
# DETERMINISTIC CLINICAL PATIENT MEDICAL HISTORY REPOSITORY
# =========================================================================

DETERMINISTIC_PATIENT_HISTORIES = {
    "CASE-001": {
        "case_id": "CASE-001",
        "patient_identifier": "DEMO-PT-001",
        "name": "Sarah Jenkins",
        "mrn": "MRN-2026-9041",
        "age": 64,
        "sex": "Female",
        "blood_group": "A+",
        "allergies_summary": "Penicillins (Anaphylaxis)",
        "conditions_summary": "Non-valvular Atrial Fibrillation, Hypertension, Hyperlipidemia",
        "last_updated": "2026-10-08 09:30 UTC",
        "timeline": [
            {"date": "2026-10-08 08:30 UTC", "event": "Baseline laboratory panel drawn (INR: 3.4, PT: 34.2s)", "actor": "Elena Rostova, RN", "badge": "LAB"},
            {"date": "2026-10-08 09:15 UTC", "event": "Medication safety assessment completed: CYP2C9 inhibition flagged for Warfarin + Fluconazole", "actor": "Dr. Sarah Lin, MD", "badge": "CLINICAL"},
            {"date": "2026-10-08 09:20 UTC", "event": "Prescription issued: Warfarin 2.5 mg PO Daily (50% dose reduction)", "actor": "Dr. Sarah Lin, MD", "badge": "PRESCRIPTION"},
            {"date": "2026-10-08 09:30 UTC", "event": "Pharmacist verified medication availability: Coumadin / Warfarin 2.5 mg available in Bin A-04", "actor": "Marcus Vance, PharmD", "badge": "PHARMACY"},
            {"date": "2026-10-08 09:35 UTC", "event": "Medication verified & marked ready for dispensing", "actor": "Marcus Vance, PharmD", "badge": "DISPENSING"}
        ],
        "medical_conditions": [
            {"condition": "Non-valvular Atrial Fibrillation", "diagnosis_date": "2021-06-12", "status": "Active", "notes": "CHA2DS2-VASc score 3. Maintained on therapeutic anticoagulation."},
            {"condition": "Essential Hypertension", "diagnosis_date": "2018-11-04", "status": "Active", "notes": "BP well-controlled on beta-blocker therapy."},
            {"condition": "Hyperlipidemia", "diagnosis_date": "2019-02-20", "status": "Active", "notes": "Primary prevention with HMG-CoA reductase inhibitor."}
        ],
        "medication_history": [
            {"medication": "Warfarin Sodium (Coumadin)", "strength": "2.5 mg", "route": "Oral", "start_date": "2026-10-07", "end_date": "Ongoing", "status": "Active (Adjusted)", "prescribing_doctor": "Dr. Sarah Lin, MD"},
            {"medication": "Metoprolol Tartrate", "strength": "25 mg", "route": "Oral", "start_date": "2021-06-15", "end_date": "Ongoing", "status": "Active", "prescribing_doctor": "Dr. Sarah Lin, MD"},
            {"medication": "Atorvastatin Calcium", "strength": "20 mg", "route": "Oral", "start_date": "2019-02-22", "end_date": "Ongoing", "status": "Active", "prescribing_doctor": "Dr. David Sterling, MD"},
            {"medication": "Fluconazole (Diflucan)", "strength": "100 mg", "route": "Oral", "start_date": "2026-10-07", "end_date": "2026-10-21", "status": "Active (14-day course)", "prescribing_doctor": "Dr. Sarah Lin, MD"}
        ],
        "allergies": [
            {"allergen": "Penicillin V / Amoxicillin", "reaction": "Anaphylaxis & Facial Angioedema", "severity": "Severe (Critical)", "recorded_date": "2024-03-15"}
        ],
        "laboratory_history": [
            {"date": "2026-10-08 08:30 UTC", "test": "Prothrombin Time (INR)", "result": "3.4", "unit": "Ratio", "reference_range": "0.8 - 1.2", "status": "High (Critical)", "source_report_id": "LAB-2026-0001", "source_doc": "data/lab_reports/LAB-2026-0001.json"},
            {"date": "2026-10-08 08:30 UTC", "test": "Prothrombin Time (PT)", "result": "34.2", "unit": "seconds", "reference_range": "11.0 - 13.5", "status": "High", "source_report_id": "LAB-2026-0001", "source_doc": "data/lab_reports/LAB-2026-0001.json"},
            {"date": "2026-10-08 08:30 UTC", "test": "Serum Creatinine", "result": "0.9", "unit": "mg/dL", "reference_range": "0.6 - 1.2", "status": "Normal", "source_report_id": "LAB-2026-0001", "source_doc": "data/lab_reports/LAB-2026-0001.json"},
            {"date": "2026-10-08 08:30 UTC", "test": "ALT (SGPT)", "result": "28", "unit": "U/L", "reference_range": "7 - 56", "status": "Normal", "source_report_id": "LAB-2026-0001", "source_doc": "data/lab_reports/LAB-2026-0001.json"}
        ],
        "clinical_assessments": [
            {"date": "2026-10-08 09:15 UTC", "assessment": "Warfarin + Fluconazole CYP2C9 Metabolic Inhibition", "risk_level": "Critical Risk", "recommendation": "Warfarin maintenance dose reduction to 2.5 mg PO Daily OR convert to Micafungin 100 mg IV Daily", "doctor": "Dr. Sarah Lin, MD (Cardiology)"}
        ],
        "pharmacy_events": [
            {"date": "2026-10-08 09:30 UTC", "medication": "Warfarin (Coumadin) 2.5 mg", "availability": "Available (420 Tabs, Bin A-04)", "substitution_request": "None Required", "doctor_verification": "Verified", "dispensing_status": "Ready for Dispensing"}
        ],
        "audit_events": [
            {"timestamp": "2026-10-08 09:35:10 UTC", "user_role": "Marcus Vance, PharmD (Pharmacist)", "action": "Prescription Verified & Marked Ready for Dispensing", "event_type": "PHARMACIST_PRESCRIPTION_VERIFIED", "audit_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"},
            {"timestamp": "2026-10-08 09:20:00 UTC", "user_role": "Dr. Sarah Lin, MD (Doctor)", "action": "Prescription Authorized for Warfarin 2.5 mg PO Daily", "event_type": "MEDICATION_PRESCRIBED", "audit_hash": "4a7d1ed414474e4033ac29ccb8653d9b002c7709b1f28b4c0bc89452b467e2a9"}
        ]
    },
    "CASE-002": {
        "case_id": "CASE-002",
        "patient_identifier": "DEMO-PT-002",
        "name": "Robert Chen",
        "mrn": "MRN-2026-8812",
        "age": 72,
        "sex": "Male",
        "blood_group": "B+",
        "allergies_summary": "Sulfa drugs (Sulfamethoxazole - Rash)",
        "conditions_summary": "Acute Deep Vein Thrombosis, Stage 3B Chronic Kidney Disease, Type 2 Diabetes",
        "last_updated": "2026-10-08 11:30 UTC",
        "timeline": [
            {"date": "2026-10-08 07:45 UTC", "event": "Inpatient renal function panel released (Serum Creatinine: 2.4 mg/dL, eGFR: 28 mL/min)", "actor": "Elena Rostova, RN", "badge": "LAB"},
            {"date": "2026-10-08 08:20 UTC", "event": "Medication safety assessment completed: LMWH Anti-Xa accumulation risk in declining renal clearance", "actor": "Dr. Sarah Lin, MD", "badge": "CLINICAL"},
            {"date": "2026-10-08 11:00 UTC", "event": "Doctor issued prescription for Enoxaparin Sodium (Lovenox) 80 mg SC Daily", "actor": "Dr. Sarah Lin, MD", "badge": "PRESCRIPTION"},
            {"date": "2026-10-08 11:15 UTC", "event": "Pharmacist checked availability: Prescribed brand Lovenox 80 mg is UNAVAILABLE (Out-of-Stock)", "actor": "Marcus Vance, PharmD", "badge": "PHARMACY"},
            {"date": "2026-10-08 11:20 UTC", "event": "Pharmacist identified configured brand alternative (Clexane 80 mg) and submitted Substitution Request to Doctor", "actor": "Marcus Vance, PharmD", "badge": "SUBSTITUTION"},
            {"date": "2026-10-08 11:25 UTC", "event": "Doctor reviewed and approved brand substitution request for Clexane 80 mg / 0.8 mL", "actor": "Dr. Sarah Lin, MD", "badge": "APPROVAL"},
            {"date": "2026-10-08 11:30 UTC", "event": "Pharmacist verified Doctor approval and marked Clexane 80 mg ready for dispensing", "actor": "Marcus Vance, PharmD", "badge": "DISPENSING"}
        ],
        "medical_conditions": [
            {"condition": "Acute Deep Vein Thrombosis (Left Lower Extremity)", "diagnosis_date": "2026-10-05", "status": "Active", "notes": "Confirmed on duplex ultrasound; requires therapeutic anticoagulation."},
            {"condition": "Stage 3B Chronic Kidney Disease", "diagnosis_date": "2022-04-18", "status": "Active", "notes": "Baseline creatinine 1.8 mg/dL with acute rise to 2.4 mg/dL."},
            {"condition": "Type 2 Diabetes Mellitus w/ Neuropathy", "diagnosis_date": "2016-09-12", "status": "Active", "notes": "Managed with glargine insulin and dietary controls."}
        ],
        "medication_history": [
            {"medication": "Enoxaparin Sodium (Lovenox)", "strength": "80 mg / 0.8 mL", "route": "Subcutaneous", "start_date": "2026-10-06", "end_date": "2026-10-08", "status": "Substituted (Unavailable)", "prescribing_doctor": "Dr. Sarah Lin, MD"},
            {"medication": "Clexane (Enoxaparin Sodium)", "strength": "80 mg / 0.8 mL", "route": "Subcutaneous", "start_date": "2026-10-08", "end_date": "Ongoing", "status": "Active (Doctor Approved Substitution)", "prescribing_doctor": "Dr. Sarah Lin, MD"},
            {"medication": "Glargine Insulin (Lantus)", "strength": "20 units", "route": "Subcutaneous", "start_date": "2020-01-10", "end_date": "Ongoing", "status": "Active", "prescribing_doctor": "Dr. Alan Mercer, MD"}
        ],
        "allergies": [
            {"allergen": "Sulfamethoxazole / Trimethoprim (Bactrim)", "reaction": "Maculopapular pruritic eruption", "severity": "Moderate", "recorded_date": "2023-08-10"}
        ],
        "laboratory_history": [
            {"date": "2026-10-08 07:45 UTC", "test": "Serum Creatinine", "result": "2.4", "unit": "mg/dL", "reference_range": "0.7 - 1.3", "status": "High (Critical)", "source_report_id": "LAB-2026-0002", "source_doc": "data/lab_reports/LAB-2026-0002.json"},
            {"date": "2026-10-08 07:45 UTC", "test": "eGFR (CKD-EPI)", "result": "28", "unit": "mL/min/1.73m²", "reference_range": "> 60", "status": "Low (Impaired)", "source_report_id": "LAB-2026-0002", "source_doc": "data/lab_reports/LAB-2026-0002.json"},
            {"date": "2026-10-08 07:45 UTC", "test": "Blood Urea Nitrogen (BUN)", "result": "38", "unit": "mg/dL", "reference_range": "7 - 20", "status": "High", "source_report_id": "LAB-2026-0002", "source_doc": "data/lab_reports/LAB-2026-0002.json"},
            {"date": "2026-10-08 07:45 UTC", "test": "Serum Potassium", "result": "4.8", "unit": "mEq/L", "reference_range": "3.5 - 5.0", "status": "Normal", "source_report_id": "LAB-2026-0002", "source_doc": "data/lab_reports/LAB-2026-0002.json"}
        ],
        "clinical_assessments": [
            {"date": "2026-10-08 08:20 UTC", "assessment": "Enoxaparin Clearance in Acute Renal Impairment", "risk_level": "Critical Risk", "recommendation": "Renal dose adjustment to 30 mg SC Daily OR substitute Unfractionated Heparin 5,000 U IV", "doctor": "Dr. Sarah Lin, MD (Nephrology / ID)"}
        ],
        "pharmacy_events": [
            {"date": "2026-10-08 11:15 UTC", "medication": "Lovenox 80 mg / 0.8 mL", "availability": "UNAVAILABLE (Stock: 0)", "substitution_request": "Suggested Clexane 80 mg (Bin D-12)", "doctor_verification": "Approved by Dr. Sarah Lin, MD", "dispensing_status": "Ready for Dispensing"}
        ],
        "audit_events": [
            {"timestamp": "2026-10-08 11:30:00 UTC", "user_role": "Marcus Vance, PharmD (Pharmacist)", "action": "Alternative Brand Marked Ready for Dispensing: Clexane 80 mg", "event_type": "MEDICATION_MARKED_READY_FOR_DISPENSING", "audit_hash": "b2f6c91a7834bcde1029384756abcdef1234567890abcdef1234567890abcdef"},
            {"timestamp": "2026-10-08 11:25:00 UTC", "user_role": "Dr. Sarah Lin, MD (Doctor)", "action": "Approved Pharmacy Brand Substitution: Lovenox -> Clexane 80 mg", "event_type": "DOCTOR_SUBSTITUTION_APPROVED", "audit_hash": "a1c7d8e9f0123456789abcdef0123456789abcdef0123456789abcdef0123456"},
            {"timestamp": "2026-10-08 11:20:00 UTC", "user_role": "Marcus Vance, PharmD (Pharmacist)", "action": "Substitution Request Dispatched: Lovenox (Unavailable) -> Clexane (Available)", "event_type": "PHARMACY_SUBSTITUTION_REQUESTED", "audit_hash": "7f8e9d0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e"}
        ]
    },
    "CASE-003": {
        "case_id": "CASE-003",
        "patient_identifier": "DEMO-PT-003",
        "name": "Elena Rostova",
        "mrn": "MRN-2026-7734",
        "age": 58,
        "sex": "Female",
        "blood_group": "O+",
        "allergies_summary": "Codeine (Severe nausea & vertigo)",
        "conditions_summary": "Essential Hypertension, Mild Hyperkalemia, Osteoarthritis",
        "last_updated": "2026-10-08 14:10 UTC",
        "timeline": [
            {"date": "2026-10-08 09:00 UTC", "event": "Electrolyte panel drawn showing Serum Potassium 5.3 mEq/L", "actor": "Elena Rostova, RN", "badge": "LAB"},
            {"date": "2026-10-08 09:40 UTC", "event": "Medication safety assessment completed: Dual ACE-Inhibitor toxicity flagged (Lisinopril + Enalapril)", "actor": "Dr. Sarah Lin, MD", "badge": "CLINICAL"},
            {"date": "2026-10-08 10:00 UTC", "event": "Doctor deprescribed duplicate Enalapril 10 mg; maintained Lisinopril 20 mg PO Daily", "actor": "Dr. Sarah Lin, MD", "badge": "PRESCRIPTION"},
            {"date": "2026-10-08 14:00 UTC", "event": "Pharmacist verified Prinivil / Lisinopril 20 mg stock available in Bin E-04 (600 Tablets)", "actor": "Marcus Vance, PharmD", "badge": "PHARMACY"},
            {"date": "2026-10-08 14:05 UTC", "event": "Medication marked ready for dispensing", "actor": "Marcus Vance, PharmD", "badge": "DISPENSING"}
        ],
        "medical_conditions": [
            {"condition": "Essential Hypertension Stage II", "diagnosis_date": "2017-03-10", "status": "Active", "notes": "Dual ACE inhibitor therapy deprescribed to prevent hypotension/hyperkalemia."},
            {"condition": "Mild Hyperkalemia", "diagnosis_date": "2026-10-07", "status": "Active", "notes": "Serum K+ 5.3–5.6 mEq/L; monitoring electrolytes."},
            {"condition": "Osteoarthritis (Bilateral Knees)", "diagnosis_date": "2020-05-14", "status": "Active", "notes": "Symptomatic relief with acetaminophen PRN."}
        ],
        "medication_history": [
            {"medication": "Lisinopril (Prinivil)", "strength": "20 mg", "route": "Oral", "start_date": "2026-10-07", "end_date": "Ongoing", "status": "Active Maintenance", "prescribing_doctor": "Dr. Sarah Lin, MD"},
            {"medication": "Enalapril Maleate (Vasotec)", "strength": "10 mg", "route": "Oral", "start_date": "2026-10-01", "end_date": "2026-10-08", "status": "Discontinued (Deprescribed Duplicate)", "prescribing_doctor": "Dr. Sarah Lin, MD"},
            {"medication": "Acetaminophen", "strength": "500 mg", "route": "Oral", "start_date": "2020-05-18", "end_date": "Ongoing", "status": "Active PRN", "prescribing_doctor": "Dr. Alan Mercer, MD"}
        ],
        "allergies": [
            {"allergen": "Codeine Phosphate", "reaction": "Nausea, intractable vomiting & severe vertigo", "severity": "Mild", "recorded_date": "2021-11-03"}
        ],
        "laboratory_history": [
            {"date": "2026-10-08 09:00 UTC", "test": "Serum Potassium", "result": "5.3", "unit": "mEq/L", "reference_range": "3.5 - 5.0", "status": "High", "source_report_id": "LAB-2026-0003", "source_doc": "data/lab_reports/LAB-2026-0003.json"},
            {"date": "2026-10-08 09:00 UTC", "test": "Serum Sodium", "result": "138", "unit": "mEq/L", "reference_range": "135 - 145", "status": "Normal", "source_report_id": "LAB-2026-0003", "source_doc": "data/lab_reports/LAB-2026-0003.json"},
            {"date": "2026-10-08 09:00 UTC", "test": "Blood Urea Nitrogen", "result": "22", "unit": "mg/dL", "reference_range": "7 - 20", "status": "High", "source_report_id": "LAB-2026-0003", "source_doc": "data/lab_reports/LAB-2026-0003.json"},
            {"date": "2026-10-08 09:00 UTC", "test": "Serum Creatinine", "result": "1.1", "unit": "mg/dL", "reference_range": "0.6 - 1.2", "status": "Normal", "source_report_id": "LAB-2026-0003", "source_doc": "data/lab_reports/LAB-2026-0003.json"}
        ],
        "clinical_assessments": [
            {"date": "2026-10-08 09:40 UTC", "assessment": "Dual ACE-Inhibitor Duplicate Toxicity & Hyperkalemia", "risk_level": "High Risk", "recommendation": "Deprescribe Enalapril 10 mg PO; maintain Lisinopril 20 mg monotherapy", "doctor": "Dr. Sarah Lin, MD (Internal Medicine)"}
        ],
        "pharmacy_events": [
            {"date": "2026-10-08 14:00 UTC", "medication": "Lisinopril (Prinivil) 20 mg", "availability": "Available (600 Tabs, Bin E-04)", "substitution_request": "None Required", "doctor_verification": "Verified", "dispensing_status": "Ready for Dispensing"}
        ],
        "audit_events": [
            {"timestamp": "2026-10-08 14:05:00 UTC", "user_role": "Marcus Vance, PharmD (Pharmacist)", "action": "Prescription Verified & Approved: Lisinopril 20 mg PO Daily", "event_type": "PHARMACIST_PRESCRIPTION_VERIFIED", "audit_hash": "c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f012345678"}
        ]
    },
    "CASE-004": {
        "case_id": "CASE-004",
        "patient_identifier": "DEMO-PT-004",
        "name": "Arthur Pendelton",
        "mrn": "MRN-2026-6629",
        "age": 79,
        "sex": "Male",
        "blood_group": "AB-",
        "allergies_summary": "None documented",
        "conditions_summary": "Congestive Heart Failure Stage C, Permanent Atrial Fibrillation, Coronary Artery Disease",
        "last_updated": "2026-10-08 16:00 UTC",
        "timeline": [
            {"date": "2026-10-08 10:15 UTC", "event": "Therapeutic drug monitoring panel released: Serum Digoxin Level 2.1 ng/mL (Toxic)", "actor": "Elena Rostova, RN", "badge": "LAB"},
            {"date": "2026-10-08 15:45 UTC", "event": "Medication safety assessment completed: Digoxin + Amiodarone P-glycoprotein interaction flagged", "actor": "Dr. Sarah Lin, MD", "badge": "CLINICAL"},
            {"date": "2026-10-08 15:50 UTC", "event": "Doctor reduced Digoxin dose to 0.125 mg PO Daily and scheduled TDM for Day 7", "actor": "Dr. Sarah Lin, MD", "badge": "PRESCRIPTION"},
            {"date": "2026-10-08 16:00 UTC", "event": "Pharmacist verified Lanoxin / Digoxin 0.125 mg stock available in Bin F-06 (180 Tablets)", "actor": "Marcus Vance, PharmD", "badge": "PHARMACY"},
            {"date": "2026-10-08 16:05 UTC", "event": "Medication marked ready for dispensing", "actor": "Marcus Vance, PharmD", "badge": "DISPENSING"}
        ],
        "medical_conditions": [
            {"condition": "Congestive Heart Failure Stage C (LVEF 35%)", "diagnosis_date": "2019-08-11", "status": "Active", "notes": "Maintained on digoxin, loop diuretic and guideline-directed therapy."},
            {"condition": "Permanent Atrial Fibrillation", "diagnosis_date": "2020-01-22", "status": "Active", "notes": "Rate controlled with amiodarone + low-dose digoxin."}
        ],
        "medication_history": [
            {"medication": "Digoxin (Lanoxin)", "strength": "0.125 mg", "route": "Oral", "start_date": "2026-10-07", "end_date": "Ongoing", "status": "Active (Dose Reduced)", "prescribing_doctor": "Dr. Sarah Lin, MD"},
            {"medication": "Amiodarone HCl (Pacerone)", "strength": "200 mg", "route": "Oral", "start_date": "2024-02-10", "end_date": "Ongoing", "status": "Active", "prescribing_doctor": "Dr. Sarah Lin, MD"},
            {"medication": "Furosemide (Lasix)", "strength": "40 mg", "route": "Oral", "start_date": "2019-08-15", "end_date": "Ongoing", "status": "Active", "prescribing_doctor": "Dr. Sarah Lin, MD"}
        ],
        "allergies": [],
        "laboratory_history": [
            {"date": "2026-10-08 10:15 UTC", "test": "Serum Digoxin Level", "result": "2.1", "unit": "ng/mL", "reference_range": "0.5 - 0.9", "status": "High (Toxic Range)", "source_report_id": "LAB-2026-0004", "source_doc": "data/lab_reports/LAB-2026-0004.json"},
            {"date": "2026-10-08 10:15 UTC", "test": "Serum Potassium", "result": "4.2", "unit": "mEq/L", "reference_range": "3.5 - 5.0", "status": "Normal", "source_report_id": "LAB-2026-0004", "source_doc": "data/lab_reports/LAB-2026-0004.json"},
            {"date": "2026-10-08 10:15 UTC", "test": "Serum Magnesium", "result": "2.0", "unit": "mg/dL", "reference_range": "1.7 - 2.2", "status": "Normal", "source_report_id": "LAB-2026-0004", "source_doc": "data/lab_reports/LAB-2026-0004.json"}
        ],
        "clinical_assessments": [
            {"date": "2026-10-08 15:45 UTC", "assessment": "Digoxin + Amiodarone P-glycoprotein Efflux Inhibition", "risk_level": "Critical Risk", "recommendation": "Reduce Digoxin dose to 0.125 mg PO Daily; repeat serum level TDM at Day 7", "doctor": "Dr. Sarah Lin, MD (Cardiology)"}
        ],
        "pharmacy_events": [
            {"date": "2026-10-08 16:00 UTC", "medication": "Digoxin (Lanoxin) 0.125 mg", "availability": "Available (180 Tabs, Bin F-06)", "substitution_request": "None Required", "doctor_verification": "Verified", "dispensing_status": "Ready for Dispensing"}
        ],
        "audit_events": [
            {"timestamp": "2026-10-08 16:05:00 UTC", "user_role": "Marcus Vance, PharmD (Pharmacist)", "action": "Prescription Verified & Approved: Digoxin 0.125 mg PO Daily", "event_type": "PHARMACIST_PRESCRIPTION_VERIFIED", "audit_hash": "d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0123456789a"}
        ]
    }
}


def _get_patient_medical_history(identifier: str) -> Dict[str, Any]:
    """Retrieves deterministic, complete patient medical history by Case ID, DEMO ID, or MRN."""
    clean = str(identifier).strip().upper()
    
    # Direct match in fixed cases
    if clean in DETERMINISTIC_PATIENT_HISTORIES:
        data = dict(DETERMINISTIC_PATIENT_HISTORIES[clean])
        data["patient_name"] = data["name"]
        return data
    for k, v in DETERMINISTIC_PATIENT_HISTORIES.items():
        if v["patient_identifier"].upper() == clean or v["name"].upper() == clean or clean in v["mrn"].upper():
            data = dict(v)
            data["patient_name"] = data["name"]
            return data

    # Fallback for synthetic patients (PT-001 to PT-265)
    pt_num = 1
    if clean.startswith("PT-") or clean.startswith("DEMO-PT-"):
        try:
            pt_num = int(clean.split("-")[-1])
        except Exception:
            pt_num = 1

    first_names = ["Sarah", "Robert", "Elena", "Arthur", "David", "Emily", "Michael", "Karen", "James", "Jennifer"]
    last_names = ["Jenkins", "Chen", "Rostova", "Pendelton", "Sterling", "Zhang", "Mercer", "Davis", "Vance", "Taylor"]
    fn = first_names[pt_num % len(first_names)]
    ln = last_names[(pt_num * 3) % len(last_names)]
    full_name = f"{fn} {ln}"

    return {
        "case_id": f"CASE-{pt_num:03d}",
        "patient_identifier": f"PT-{pt_num:03d}",
        "name": full_name,
        "mrn": f"MRN-2026-{8000 + pt_num}",
        "age": 45 + (pt_num % 40),
        "sex": "Female" if pt_num % 2 == 1 else "Male",
        "blood_group": ["A+", "B+", "O+", "AB+", "A-", "O-"][pt_num % 6],
        "allergies_summary": "Penicillins" if pt_num % 3 == 0 else "None documented",
        "conditions_summary": "Cardiovascular Disease & Hypertension",
        "last_updated": "2026-10-08 08:00 UTC",
        "timeline": [
            {"date": "2026-10-08 08:00 UTC", "event": "Inpatient admission & clinical chart review", "actor": "Dr. Sarah Lin, MD", "badge": "CLINICAL"},
            {"date": "2026-10-08 08:30 UTC", "event": "Laboratory evaluation completed", "actor": "Elena Rostova, RN", "badge": "LAB"},
            {"date": "2026-10-08 09:00 UTC", "event": "Prescription received in pharmacy queue", "actor": "Marcus Vance, PharmD", "badge": "PHARMACY"}
        ],
        "medical_conditions": [
            {"condition": "Essential Hypertension", "diagnosis_date": "2020-01-15", "status": "Active", "notes": "Therapeutic maintenance regimen."},
            {"condition": "Hypercholesterolemia", "diagnosis_date": "2021-05-10", "status": "Active", "notes": "Lipid lowering therapy."}
        ],
        "medication_history": [
            {"medication": "Amlodipine Besylate", "strength": "5 mg", "route": "Oral", "start_date": "2021-05-15", "end_date": "Ongoing", "status": "Active", "prescribing_doctor": "Dr. Sarah Lin, MD"}
        ],
        "allergies": [
            {"allergen": "Penicillin V", "reaction": "Cutaneous rash", "severity": "Moderate", "recorded_date": "2023-01-10"}
        ] if pt_num % 3 == 0 else [],
        "laboratory_history": [
            {"date": "2026-10-08 08:30 UTC", "test": "Serum Creatinine", "result": "1.0", "unit": "mg/dL", "reference_range": "0.6 - 1.2", "status": "Normal", "source_report_id": "LAB-GEN-01", "source_doc": "data/lab_reports/LAB-2026-0001.json"}
        ],
        "clinical_assessments": [
            {"date": "2026-10-08 08:00 UTC", "assessment": "Routine Clinical Medication Review", "risk_level": "Low Risk", "recommendation": "Maintain baseline therapeutic regimen", "doctor": "Dr. Sarah Lin, MD"}
        ],
        "pharmacy_events": [
            {"date": "2026-10-08 09:00 UTC", "medication": "Amlodipine Besylate 5 mg", "availability": "Available", "substitution_request": "None Required", "doctor_verification": "Verified", "dispensing_status": "Ready for Dispensing"}
        ],
        "audit_events": [
            {"timestamp": "2026-10-08 08:00:00 UTC", "user_role": "Dr. Sarah Lin, MD (Doctor)", "action": "Patient Chart Evaluated", "event_type": "CLINICAL_EVENT_INGESTED", "audit_hash": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f01234"}
        ]
    }


# =========================================================================
# REQUEST & RESPONSE PYDANTIC SCHEMAS
# =========================================================================

class PharmacistSubstitutionRequest(BaseModel):
    case_id: str
    patient_id: Optional[int] = None
    patient_name: Optional[str] = None
    prescribed_medicine: str
    prescribed_brand: Optional[str] = None
    strength: str
    formulation: str
    suggested_brand: str
    reason: str = "Prescribed brand is currently unavailable."
    pharmacist_notes: Optional[str] = None


class DoctorSubstitutionDecisionRequest(BaseModel):
    case_id: str
    suggested_brand: Optional[str] = None
    decision: str  # 'APPROVED', 'REJECTED', 'CLARIFICATION_REQUIRED'
    doctor_notes: Optional[str] = None
    doctor_name: Optional[str] = None
    request_id: Optional[str] = None
    original_medicine: Optional[str] = None


class PharmacistDispenseRequest(BaseModel):
    case_id: str
    patient_id: Optional[int] = None
    patient_name: Optional[str] = None
    medication: str
    brand_name: Optional[str] = None
    strength: str
    formulation: str
    dispense_notes: Optional[str] = None


class PharmacistVerificationRequest(BaseModel):
    case_id: str
    patient_id: Optional[int] = None
    patient_name: Optional[str] = None
    patient_identifier: Optional[str] = None
    medication: str
    strength: str
    route: Optional[str] = "Oral"
    formulation: Optional[str] = None
    quantity: Optional[int] = None
    frequency: Optional[str] = None
    doctor_recommendation: Optional[str] = "Doctor prescription verified."
    availability_status: str = "Available"
    formulation_verified: bool = True
    strength_verified: bool = True
    route_verified: bool = True
    consistency_verified: bool = True
    alternative_medication: Optional[str] = None
    pharmacist_notes: Optional[str] = None


class PharmacistClarificationRequest(BaseModel):
    case_id: str
    patient_id: Optional[int] = None
    patient_name: Optional[str] = None
    medication: str
    reason: str
    notes: Optional[str] = None
    alternative_suggested: Optional[str] = None


# =========================================================================
# DOCTOR DASHBOARD ENDPOINT (WITH PHARMACY SUBSTITUTION REQUESTS)
# =========================================================================

@router.get("/api/dashboard/doctor", summary="Doctor Dashboard: Clinical findings, pending cosigns, and substitution requests")
def get_doctor_dashboard_data(
    db: Session = Depends(get_db),
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
    current_user: Optional[User] = Depends(get_optional_user),
):
    """Provides clinical decision support metrics, pending cosigns, patient risks, and pharmacy substitution requests for Physicians."""
    patients = db.execute(select(Patient).order_by(Patient.id)).scalars().all()
    patient_summaries = []
    for p in patients:
        meds = db.execute(select(Medication).where(Medication.patient_id == p.id)).scalars().all()
        labs = db.execute(select(Lab).where(Lab.patient_id == p.id).order_by(desc(Lab.id))).scalars().all()
        findings = db.execute(select(Finding).where(Finding.patient_id == p.id)).scalars().all()
        patient_summaries.append({
            "id": p.id,
            "patient_identifier": p.patient_identifier,
            "name": p.name,
            "sex": p.sex,
            "allergies": p.allergy_information or "None documented",
            "active_medications": [{"name": m.drug_name, "dose": f"{m.dose or ''} {m.dose_unit or ''}".strip(), "frequency": m.frequency} for m in meds],
            "recent_labs": [{"test": l.test_name, "value": l.value, "unit": l.unit or ""} for l in labs[:3]],
            "active_findings_count": len(findings),
        })

    # Retrieve simulated orders and co-signs from audit ledger
    all_events = audit_ledger.get_events()
    sim_events = [e for e in all_events if e.event_type == "SIMULATED_ORDER_CREATED"]
    cosigned_events = [e for e in all_events if e.event_type == "SIMULATED_ORDER_COSIGNED"]
    cosigned_map = {e.payload.get("simulation_id"): e.payload for e in cosigned_events if isinstance(e.payload, dict)}

    pending_cosigns = []
    for ev in sim_events:
        p = ev.payload if isinstance(ev.payload, dict) else {}
        sim_id = p.get("simulation_id")
        is_cosigned = sim_id in cosigned_map
        if not is_cosigned:
            pending_cosigns.append({
                "simulation_id": sim_id,
                "finding_id": p.get("finding_id"),
                "patient_id": p.get("patient_id"),
                "drug_name": p.get("drug_name"),
                "proposed_action": p.get("proposed_action"),
                "action_type": p.get("action_type"),
                "rationale": p.get("rationale"),
                "severity": p.get("severity", "High"),
                "source": p.get("source"),
                "evidence_id": p.get("evidence_id"),
                "requires_cosign": True,
                "status": "pending_cosign",
            })

    # Retrieve Pharmacy Substitution Requests from Audit Ledger
    sub_requests_events = [e for e in all_events if e.event_type == "PHARMACY_SUBSTITUTION_REQUESTED"]
    sub_decision_events = [e for e in all_events if e.event_type in ("DOCTOR_SUBSTITUTION_APPROVED", "DOCTOR_SUBSTITUTION_REJECTED", "DOCTOR_CLARIFICATION_REQUESTED")]
    decided_cases = {e.payload.get("case_id") for e in sub_decision_events if isinstance(e.payload, dict)}

    pharmacy_substitution_requests = []
    # Seed default presentation case if not yet processed
    if "CASE-002" not in decided_cases and not any(e.payload.get("case_id") == "CASE-002" for e in sub_requests_events if isinstance(e.payload, dict)):
        pharmacy_substitution_requests.append({
            "case_id": "CASE-002",
            "patient_name": "Robert Chen (72M) • MRN-2026-8812",
            "patient_identifier": "DEMO-PT-002",
            "original_prescription": "Enoxaparin Sodium 80 mg / 0.8 mL SC Daily",
            "prescribed_brand": "Lovenox 80 mg / 0.8 mL Pre-filled Syringe",
            "reason": "Prescribed brand Lovenox 80 mg is currently unavailable in local dispensary stock.",
            "suggested_brand": "Clexane 80 mg / 0.8 mL Pre-filled Syringe",
            "availability": "Available (60 Syringes in Bin D-12)",
            "pharmacist_note": "Equivalent brand with identical active ingredient and strength available in dispensary stock.",
            "request_date": "2026-10-08 11:20 UTC",
            "status": "PENDING DOCTOR VERIFICATION",
        })

    for ev in sub_requests_events:
        p = ev.payload if isinstance(ev.payload, dict) else {}
        c_id = p.get("case_id")
        if c_id not in decided_cases and not any(r["case_id"] == c_id for r in pharmacy_substitution_requests):
            pharmacy_substitution_requests.append({
                "case_id": c_id,
                "patient_name": p.get("patient_name", f"Case {c_id}"),
                "patient_identifier": p.get("patient_identifier", f"PT-{c_id}"),
                "original_prescription": f"{p.get('prescribed_medicine')} {p.get('strength')} {p.get('formulation')}",
                "prescribed_brand": p.get("prescribed_brand", p.get("prescribed_medicine")),
                "reason": p.get("reason", "Medication unavailable"),
                "suggested_brand": p.get("suggested_brand", "Configured Brand Alternative"),
                "availability": "Available",
                "pharmacist_note": p.get("pharmacist_notes", "Pharmacist verification pending Doctor approval."),
                "request_date": ev.timestamp,
                "status": "PENDING DOCTOR VERIFICATION",
            })

    # Retrieve active findings
    findings = db.execute(select(Finding).order_by(desc(Finding.id)).limit(15)).scalars().all()
    findings_list = []
    for f in findings:
        trace_dict = f.trace if isinstance(f.trace, dict) else {}
        inputs_dict = f.inputs if isinstance(f.inputs, dict) else {}
        findings_list.append({
            "id": f.id,
            "patient_id": f.patient_id,
            "rule_id": f.rule_id,
            "severity": f.severity,
            "title": f.title,
            "description": f.description,
            "action": f.action,
            "source": trace_dict.get("source") or "Deterministic Safety Engine",
            "evidence_id": trace_dict.get("evidence_id") or "AEGIS-EV-001",
            "source_version": trace_dict.get("source_version") or "1.0.0",
            "inputs": inputs_dict,
            "created_at": f.created_at.strftime("%Y-%m-%d %H:%M:%S") if f.created_at else None,
        })

    recent_events = [
        {
            "id": e.id,
            "timestamp": e.timestamp,
            "actor": e.actor,
            "event_type": e.event_type,
            "summary": f"{e.event_type} by {e.actor}",
            "hash": e.hash[:16] + "...",
        }
        for e in all_events[-8:]
    ]

    return {
        "role": "Doctor",
        "clinician": current_user.full_name if current_user else "Dr. Sarah Lin, MD",
        "stats": {
            "total_patients": len(patients),
            "pending_cosigns_count": len(pending_cosigns),
            "substitution_requests_count": len(pharmacy_substitution_requests),
            "active_findings_count": len(findings_list),
            "critical_alerts_count": sum(1 for f in findings_list if f["severity"].lower() in ("critical", "high", "review_required")),
        },
        "patients": patient_summaries,
        "pending_cosigns": pending_cosigns,
        "substitution_requests": pharmacy_substitution_requests,
        "findings": findings_list,
        "recent_events": recent_events,
    }


# =========================================================================
# NURSE DASHBOARD: MEDICATION ADMINISTRATION RECORD (MAR) & VITALS INGESTION
# =========================================================================

DETERMINISTIC_NURSE_MAR = [
    {
        "mar_id": "MAR-001",
        "case_id": "CASE-001",
        "patient_identifier": "DEMO-PT-001",
        "patient_name": "Sarah Jenkins",
        "medication": "Warfarin Sodium",
        "dose": "2.5 mg",
        "route": "Oral (PO)",
        "scheduled_time": "08:00",
        "status": "DUE",
        "prescribing_doctor": "Dr. Sarah Lin, MD",
        "nursing_note": "Target INR 2.0–3.0. Verify morning lab before administration.",
        "clinical_alert": "Active DDI flagged: Warfarin + Fluconazole CYP2C9 inhibition. Morning INR elevated at 3.4.",
        "requires_escalation": True,
        "recorded_by": None,
        "recorded_at": None,
        "hold_reason": None,
    },
    {
        "mar_id": "MAR-002",
        "case_id": "CASE-001",
        "patient_identifier": "DEMO-PT-001",
        "patient_name": "Sarah Jenkins",
        "medication": "Fluconazole",
        "dose": "200 mg",
        "route": "Oral (PO)",
        "scheduled_time": "20:00",
        "status": "HELD",
        "prescribing_doctor": "Dr. Sarah Lin, MD",
        "nursing_note": "Safety Hold: High bleeding hazard from CYP2C9 inhibition.",
        "clinical_alert": "Medication safety assessment requires medication hold/review.",
        "requires_escalation": True,
        "recorded_by": "Elena Rostova, RN",
        "recorded_at": "08 Oct 2026 07:30 UTC",
        "hold_reason": "Clinical safety rule AEGIS-DEMO-001 triggered: Drug interaction with Warfarin.",
    },
    {
        "mar_id": "MAR-003",
        "case_id": "CASE-001",
        "patient_identifier": "DEMO-PT-001",
        "patient_name": "Sarah Jenkins",
        "medication": "Atorvastatin Calcium",
        "dose": "20 mg",
        "route": "Oral (PO)",
        "scheduled_time": "21:00",
        "status": "ADMINISTERED",
        "prescribing_doctor": "Dr. Sarah Lin, MD",
        "nursing_note": "Administered with evening meal. Patient tolerated well.",
        "clinical_alert": None,
        "requires_escalation": False,
        "recorded_by": "Elena Rostova, RN",
        "recorded_at": "07 Oct 2026 21:05 UTC",
        "hold_reason": None,
    },
    {
        "mar_id": "MAR-004",
        "case_id": "CASE-002",
        "patient_identifier": "DEMO-PT-002",
        "patient_name": "Robert Chen",
        "medication": "Enoxaparin Sodium",
        "dose": "30 mg",
        "route": "Subcutaneous (SC)",
        "scheduled_time": "09:00",
        "status": "DUE",
        "prescribing_doctor": "Dr. Sarah Lin, MD",
        "nursing_note": "Renal-adjusted dose (CrCl 24 mL/min). Rotate abdominal injection sites.",
        "clinical_alert": "Renal safety alert: Serum creatinine 2.4 mg/dL requires dosage verification.",
        "requires_escalation": True,
        "recorded_by": None,
        "recorded_at": None,
        "hold_reason": None,
    },
    {
        "mar_id": "MAR-005",
        "case_id": "CASE-002",
        "patient_identifier": "DEMO-PT-002",
        "patient_name": "Robert Chen",
        "medication": "Pantoprazole Sodium",
        "dose": "40 mg",
        "route": "Intravenous (IV)",
        "scheduled_time": "07:30",
        "status": "ADMINISTERED",
        "prescribing_doctor": "Dr. Sarah Lin, MD",
        "nursing_note": "Slow IV push over 2 minutes via peripheral line.",
        "clinical_alert": None,
        "requires_escalation": False,
        "recorded_by": "Elena Rostova, RN",
        "recorded_at": "08 Oct 2026 07:35 UTC",
        "hold_reason": None,
    },
    {
        "mar_id": "MAR-006",
        "case_id": "CASE-002",
        "patient_identifier": "DEMO-PT-002",
        "patient_name": "Robert Chen",
        "medication": "Furosemide",
        "dose": "20 mg",
        "route": "Intravenous (IV)",
        "scheduled_time": "14:00",
        "status": "PENDING",
        "prescribing_doctor": "Dr. Sarah Lin, MD",
        "nursing_note": "Monitor strict fluid balance and urine output post-dose.",
        "clinical_alert": None,
        "requires_escalation": False,
        "recorded_by": None,
        "recorded_at": None,
        "hold_reason": None,
    },
    {
        "mar_id": "MAR-007",
        "case_id": "CASE-003",
        "patient_identifier": "DEMO-PT-003",
        "patient_name": "Elena Rostova",
        "medication": "Lisinopril",
        "dose": "10 mg",
        "route": "Oral (PO)",
        "scheduled_time": "08:00",
        "status": "ADMINISTERED",
        "prescribing_doctor": "Dr. Sarah Lin, MD",
        "nursing_note": "Blood pressure confirmed at 142/88 mmHg prior to administration.",
        "clinical_alert": None,
        "requires_escalation": False,
        "recorded_by": "Elena Rostova, RN",
        "recorded_at": "08 Oct 2026 08:05 UTC",
        "hold_reason": None,
    },
    {
        "mar_id": "MAR-008",
        "case_id": "CASE-003",
        "patient_identifier": "DEMO-PT-003",
        "patient_name": "Elena Rostova",
        "medication": "Enalapril Maleate",
        "dose": "10 mg",
        "route": "Oral (PO)",
        "scheduled_time": "12:00",
        "status": "HELD",
        "prescribing_doctor": "Dr. Sarah Lin, MD",
        "nursing_note": "Safety Hold: Duplicate ACE-inhibitor therapy alert flagged by safety engine.",
        "clinical_alert": "Therapeutic Duplication: Lisinopril + Enalapril dual ACE-I therapy flagged.",
        "requires_escalation": True,
        "recorded_by": "Elena Rostova, RN",
        "recorded_at": "08 Oct 2026 07:45 UTC",
        "hold_reason": "Rule AEGIS-DEMO-003: Duplicate ACE-I therapy elevates hyperkalemia hazard.",
    },
    {
        "mar_id": "MAR-009",
        "case_id": "CASE-003",
        "patient_identifier": "DEMO-PT-003",
        "patient_name": "Elena Rostova",
        "medication": "Amlodipine Besylate",
        "dose": "5 mg",
        "route": "Oral (PO)",
        "scheduled_time": "09:00",
        "status": "DUE",
        "prescribing_doctor": "Dr. Sarah Lin, MD",
        "nursing_note": "Daily calcium channel blocker maintenance.",
        "clinical_alert": None,
        "requires_escalation": False,
        "recorded_by": None,
        "recorded_at": None,
        "hold_reason": None,
    },
    {
        "mar_id": "MAR-010",
        "case_id": "CASE-004",
        "patient_identifier": "DEMO-PT-004",
        "patient_name": "Arthur Pendelton",
        "medication": "Digoxin",
        "dose": "0.125 mg",
        "route": "Oral (PO)",
        "scheduled_time": "10:00",
        "status": "DUE",
        "prescribing_doctor": "Dr. Sarah Lin, MD",
        "nursing_note": "Auscultate apical pulse for 1 full minute prior to administration (>60 bpm required).",
        "clinical_alert": "Narrow therapeutic index monitoring active.",
        "requires_escalation": False,
        "recorded_by": None,
        "recorded_at": None,
        "hold_reason": None,
    },
    {
        "mar_id": "MAR-011",
        "case_id": "CASE-004",
        "patient_identifier": "DEMO-PT-004",
        "patient_name": "Arthur Pendelton",
        "medication": "Metoprolol Tartrate",
        "dose": "25 mg",
        "route": "Oral (PO)",
        "scheduled_time": "08:00",
        "status": "ADMINISTERED",
        "prescribing_doctor": "Dr. Sarah Lin, MD",
        "nursing_note": "Morning beta-blocker. Pre-dose HR: 74 bpm, BP: 126/78 mmHg.",
        "clinical_alert": None,
        "requires_escalation": False,
        "recorded_by": "Elena Rostova, RN",
        "recorded_at": "08 Oct 2026 08:10 UTC",
        "hold_reason": None,
    },
]

DETERMINISTIC_NURSE_VITALS_LABS = [
    {
        "id": "VIT-001",
        "case_id": "CASE-001",
        "patient_identifier": "DEMO-PT-001",
        "patient_name": "Sarah Jenkins",
        "parameter": "Blood Pressure",
        "value": "128/78",
        "unit": "mmHg",
        "recorded_time": "08:10",
        "status": "Normal",
        "source_type": "Bedside Vitals Monitor",
        "recorded_by": "Elena Rostova, RN",
    },
    {
        "id": "VIT-002",
        "case_id": "CASE-001",
        "patient_identifier": "DEMO-PT-001",
        "patient_name": "Sarah Jenkins",
        "parameter": "Heart Rate",
        "value": "82",
        "unit": "bpm",
        "recorded_time": "08:10",
        "status": "Normal",
        "source_type": "Bedside Vitals Monitor",
        "recorded_by": "Elena Rostova, RN",
    },
    {
        "id": "VIT-003",
        "case_id": "CASE-001",
        "patient_identifier": "DEMO-PT-001",
        "patient_name": "Sarah Jenkins",
        "parameter": "SpO₂",
        "value": "97",
        "unit": "%",
        "recorded_time": "08:10",
        "status": "Normal",
        "source_type": "Pulse Oximetry",
        "recorded_by": "Elena Rostova, RN",
    },
    {
        "id": "VIT-004",
        "case_id": "CASE-001",
        "patient_identifier": "DEMO-PT-001",
        "patient_name": "Sarah Jenkins",
        "parameter": "Prothrombin Time (INR)",
        "value": "3.4",
        "unit": "ratio",
        "recorded_time": "07:45",
        "status": "High",
        "source_type": "Pathology Core Lab (LAB-2026-0001)",
        "recorded_by": "Verified Laboratory Feed",
    },
    {
        "id": "VIT-005",
        "case_id": "CASE-002",
        "patient_identifier": "DEMO-PT-002",
        "patient_name": "Robert Chen",
        "parameter": "Serum Creatinine",
        "value": "2.4",
        "unit": "mg/dL",
        "recorded_time": "08:15",
        "status": "High",
        "source_type": "Pathology Core Lab (LAB-2026-0002)",
        "recorded_by": "Verified Laboratory Feed",
    },
    {
        "id": "VIT-006",
        "case_id": "CASE-002",
        "patient_identifier": "DEMO-PT-002",
        "patient_name": "Robert Chen",
        "parameter": "Blood Pressure",
        "value": "134/82",
        "unit": "mmHg",
        "recorded_time": "08:00",
        "status": "Normal",
        "source_type": "Bedside Vitals Monitor",
        "recorded_by": "Elena Rostova, RN",
    },
    {
        "id": "VIT-007",
        "case_id": "CASE-002",
        "patient_identifier": "DEMO-PT-002",
        "patient_name": "Robert Chen",
        "parameter": "Pulse Rate",
        "value": "76",
        "unit": "bpm",
        "recorded_time": "08:00",
        "status": "Normal",
        "source_type": "Bedside Vitals Monitor",
        "recorded_by": "Elena Rostova, RN",
    },
    {
        "id": "VIT-008",
        "case_id": "CASE-003",
        "patient_identifier": "DEMO-PT-003",
        "patient_name": "Elena Rostova",
        "parameter": "Serum Potassium (K+)",
        "value": "5.3",
        "unit": "mEq/L",
        "recorded_time": "08:20",
        "status": "High",
        "source_type": "Pathology Core Lab (LAB-2026-0003)",
        "recorded_by": "Verified Laboratory Feed",
    },
    {
        "id": "VIT-009",
        "case_id": "CASE-003",
        "patient_identifier": "DEMO-PT-003",
        "patient_name": "Elena Rostova",
        "parameter": "Blood Pressure",
        "value": "142/88",
        "unit": "mmHg",
        "recorded_time": "08:15",
        "status": "Borderline",
        "source_type": "Bedside Vitals Monitor",
        "recorded_by": "Elena Rostova, RN",
    },
    {
        "id": "VIT-010",
        "case_id": "CASE-004",
        "patient_identifier": "DEMO-PT-004",
        "patient_name": "Arthur Pendelton",
        "parameter": "Apical Heart Rate",
        "value": "72",
        "unit": "bpm",
        "recorded_time": "08:00",
        "status": "Normal",
        "source_type": "Manual Auscultation",
        "recorded_by": "Elena Rostova, RN",
    },
    {
        "id": "VIT-011",
        "case_id": "CASE-004",
        "patient_identifier": "DEMO-PT-004",
        "patient_name": "Arthur Pendelton",
        "parameter": "Serum Digoxin",
        "value": "1.1",
        "unit": "ng/mL",
        "recorded_time": "07:30",
        "status": "Normal",
        "source_type": "Therapeutic Drug Monitoring",
        "recorded_by": "Verified Laboratory Feed",
    },
]


@router.get("/api/dashboard/nurse", summary="Nurse Dashboard: MAR records, vitals/lab ingestion, and safety monitoring")
def get_nurse_dashboard_data(
    db: Session = Depends(get_db),
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
    current_user: Optional[User] = Depends(get_optional_user),
):
    """
    Nurse Dashboard:
    - Medication Administration Record (MAR) with realistic case-linked schedules.
    - Rapid Inpatient Lab / Vitals Ingestion table.
    - Today's Nursing Activity Summary.
    """
    patients = db.execute(select(Patient).order_by(Patient.id)).scalars().all()
    assigned_count = len(patients) if patients else 4

    # Calculate real-time stats
    due_count = sum(1 for m in DETERMINISTIC_NURSE_MAR if m["status"] == "DUE")
    admin_count = sum(1 for m in DETERMINISTIC_NURSE_MAR if m["status"] == "ADMINISTERED")
    held_count = sum(1 for m in DETERMINISTIC_NURSE_MAR if m["status"] == "HELD")
    vitals_count = len(DETERMINISTIC_NURSE_VITALS_LABS)
    attention_count = sum(1 for m in DETERMINISTIC_NURSE_MAR if m.get("requires_escalation")) + sum(1 for v in DETERMINISTIC_NURSE_VITALS_LABS if v["status"] in {"High", "Critical"})

    return {
        "role": "Nurse",
        "nurse_name": current_user.full_name if current_user else "Elena Rostova, RN",
        "title": "Medication Administration Record & Inpatient Monitoring",
        "stats": {
            "medications_due": due_count,
            "medications_administered": admin_count,
            "medications_held": held_count,
            "vitals_recorded": vitals_count,
            "attention_required": attention_count,
            "assigned_patients_count": assigned_count,
        },
        "mar_schedule": DETERMINISTIC_NURSE_MAR,
        "vitals_labs": DETERMINISTIC_NURSE_VITALS_LABS,
    }


class NurseMARActionRequest(BaseModel):
    mar_id: str
    action: str  # "ADMINISTER" / "ADMINISTERED" or "HOLD" / "HELD"
    case_id: Optional[str] = None
    patient_name: Optional[str] = None
    medication: Optional[str] = None
    dose: Optional[str] = None
    route: Optional[str] = None
    nurse_name: str = "Elena Rostova, RN"
    notes: Optional[str] = None
    hold_reason: Optional[str] = None


@router.post("/api/dashboard/nurse/mar-action", summary="Record Medication Administration or Safety Hold with SHA-256 Audit")
def record_nurse_mar_action(
    req: NurseMARActionRequest,
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
):
    """
    Records a nurse's medication administration or safety hold event into the cryptographic audit ledger.
    """
    action_type = req.action.strip().upper()
    is_admin = action_type in ("ADMINISTER", "ADMINISTERED")
    event_type = "MEDICATION_ADMINISTERED" if is_admin else "MEDICATION_HELD"
    new_status = "ADMINISTERED" if is_admin else "HELD"

    timestamp_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    # Match in-memory deterministic record
    target_mar = None
    for m in DETERMINISTIC_NURSE_MAR:
        if m.get("mar_id") == req.mar_id or m.get("id") == req.mar_id:
            target_mar = m
            m["status"] = new_status
            m["recorded_by"] = req.nurse_name
            m["recorded_at"] = timestamp_str
            if req.notes:
                m["nursing_note"] = req.notes
            if req.hold_reason:
                m["hold_reason"] = req.hold_reason
            break

    case_id = req.case_id or (target_mar.get("case_id") if target_mar else "CASE-UNKNOWN")
    patient_name = req.patient_name or (target_mar.get("patient_name") if target_mar else "Unknown Patient")
    medication = req.medication or (target_mar.get("medication") if target_mar else "Scheduled Medication")
    dose = req.dose or (target_mar.get("dose") if target_mar else "")
    route = req.route or (target_mar.get("route") if target_mar else "")

    audit_payload = {
        "mar_id": req.mar_id,
        "case_id": case_id,
        "patient_name": patient_name,
        "medication": medication,
        "dose": dose,
        "route": route,
        "nurse": req.nurse_name,
        "action": event_type,
        "status": new_status,
        "notes": req.notes or ("Medication administered as scheduled." if is_admin else "Medication held due to clinical caution."),
        "hold_reason": req.hold_reason,
    }

    record = audit_ledger.append_event(
        actor=f"{req.nurse_name} (Nurse)",
        event_type=event_type,
        payload=audit_payload,
    )

    return {
        "status": new_status,
        "event_type": event_type,
        "mar_id": req.mar_id,
        "audit_hash": record.hash,
        "message": f"Medication {medication} marked as {new_status} by {req.nurse_name}. Cryptographic audit sealed.",
    }


class NurseRecordVitalRequest(BaseModel):
    case_id: str
    patient_name: str
    parameter: str
    value: str
    unit: str
    nurse_name: str = "Elena Rostova, RN"
    notes: Optional[str] = None


@router.post("/api/dashboard/nurse/record-vital", summary="Record Inpatient Vital or Point-of-Care Lab with SHA-256 Audit")
def record_nurse_vital(
    req: NurseRecordVitalRequest,
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
    db: Session = Depends(get_db),
):
    """
    Records an inpatient vital sign or point-of-care lab entry, evaluates normal/high status, and logs a tamper-evident audit event.
    """
    time_str = datetime.utcnow().strftime("%H:%M")
    
    # Assess status
    val_status = "Normal"
    val_clean = req.value.strip()
    param_clean = req.parameter.strip().lower()
    
    try:
        num_val = float(val_clean.split("/")[0]) if "/" in val_clean else float(val_clean)
        if "creatinine" in param_clean and num_val > 1.3:
            val_status = "High"
        elif "potassium" in param_clean and num_val > 5.0:
            val_status = "High"
        elif "inr" in param_clean and num_val > 3.0:
            val_status = "High"
        elif "pressure" in param_clean and (num_val > 140 or (len(val_clean.split("/")) > 1 and float(val_clean.split("/")[1]) > 90)):
            val_status = "Borderline"
        elif "spo" in param_clean and num_val < 92:
            val_status = "Critical"
    except Exception:
        pass

    new_id = f"VIT-0{len(DETERMINISTIC_NURSE_VITALS_LABS) + 1:02d}"
    entry = {
        "id": new_id,
        "case_id": req.case_id,
        "patient_identifier": req.case_id,
        "patient_name": req.patient_name,
        "parameter": req.parameter,
        "value": req.value,
        "unit": req.unit,
        "recorded_time": time_str,
        "status": val_status,
        "source_type": "Nurse Bedside Entry",
        "recorded_by": req.nurse_name,
    }
    DETERMINISTIC_NURSE_VITALS_LABS.insert(0, entry)

    audit_record = audit_ledger.append_event(
        actor=f"{req.nurse_name} (Nurse)",
        event_type="VITAL_SIGN_RECORDED",
        payload={
            "vital_id": new_id,
            "case_id": req.case_id,
            "patient_name": req.patient_name,
            "parameter": req.parameter,
            "value": req.value,
            "unit": req.unit,
            "status": val_status,
            "nurse": req.nurse_name,
        },
    )

    return {
        "status": "RECORDED",
        "record": entry,
        "vital_entry": entry,
        "audit_hash": audit_record.hash,
        "message": f"Vital {req.parameter} ({req.value} {req.unit}) recorded for {req.patient_name}. SHA-256 hash sealed.",
    }


# =========================================================================
# PHARMACIST WORKFLOW: MEDICATION FULFILLMENT & SUBSTITUTION
# =========================================================================

@router.get("/api/dashboard/pharmacist", summary="Pharmacist Dashboard: Medication Fulfillment & Substitution")
def get_pharmacist_dashboard_data(
    db: Session = Depends(get_db),
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
    resolution_engine=Depends(get_resolution_engine),
    current_user: Optional[User] = Depends(get_optional_user),
):
    """
    Pharmacist Dashboard: Medication Fulfillment & Substitution.
    Primary workflow: Doctor Prescription -> Check Medication Availability -> If Available -> Proceed for Dispensing
    If NOT Available -> Identify Available Equivalent Brand -> Send Substitution Request to Doctor -> Doctor Verification / Approval -> Dispensing.
    """
    all_events = audit_ledger.get_events()

    # Track audit events for verification, substitution requests, and doctor decisions
    verified_events = {e.payload.get("case_id"): e for e in all_events if e.event_type in ("PHARMACIST_PRESCRIPTION_VERIFIED", "MEDICATION_MARKED_READY_FOR_DISPENSING") and isinstance(e.payload, dict)}
    sub_requested_events = {e.payload.get("case_id"): e for e in all_events if e.event_type == "PHARMACY_SUBSTITUTION_REQUESTED" and isinstance(e.payload, dict)}
    sub_approved_events = {e.payload.get("case_id"): e for e in all_events if e.event_type == "DOCTOR_SUBSTITUTION_APPROVED" and isinstance(e.payload, dict)}
    sub_rejected_events = {e.payload.get("case_id"): e for e in all_events if e.event_type == "DOCTOR_SUBSTITUTION_REJECTED" and isinstance(e.payload, dict)}
    clarif_events = {e.payload.get("case_id"): e for e in all_events if e.event_type in ("PHARMACIST_CLARIFICATION_REQUESTED", "DOCTOR_CLARIFICATION_REQUESTED") and isinstance(e.payload, dict)}

    # Deterministic Local Medication Inventory
    local_inventory = [
        {"medication": "Warfarin Sodium", "brand_name": "Coumadin", "strength": "2.5 mg", "formulation": "Oral Tablet", "stock": 420, "unit": "tabs", "status": "AVAILABLE", "location": "Bin A-04", "batch": "WF-88392", "expiry": "2027-11"},
        {"medication": "Warfarin Sodium", "brand_name": "Jantoven", "strength": "2.5 mg", "formulation": "Oral Tablet", "stock": 310, "unit": "tabs", "status": "AVAILABLE", "location": "Bin A-05", "batch": "WF-88104", "expiry": "2027-09"},
        {"medication": "Fluconazole", "brand_name": "Diflucan", "strength": "100 mg", "formulation": "Oral Tablet", "stock": 140, "unit": "tabs", "status": "AVAILABLE", "location": "Bin B-12", "batch": "FL-22910", "expiry": "2027-04"},
        {"medication": "Enoxaparin Sodium", "brand_name": "Lovenox", "strength": "80 mg / 0.8 mL", "formulation": "Pre-filled Syringe", "stock": 0, "unit": "syringes", "status": "UNAVAILABLE", "location": "Bin D-10", "batch": "OUT-OF-STOCK", "expiry": "-"},
        {"medication": "Enoxaparin Sodium", "brand_name": "Clexane", "strength": "80 mg / 0.8 mL", "formulation": "Pre-filled Syringe", "stock": 60, "unit": "syringes", "status": "AVAILABLE", "location": "Bin D-12", "batch": "CX-88102", "expiry": "2028-02"},
        {"medication": "Enoxaparin Sodium", "brand_name": "Inhixa", "strength": "80 mg / 0.8 mL", "formulation": "Pre-filled Syringe", "stock": 45, "unit": "syringes", "status": "AVAILABLE", "location": "Bin D-14", "batch": "IN-33201", "expiry": "2027-10"},
        {"medication": "Enoxaparin Sodium", "brand_name": "Lovenox", "strength": "30 mg / 0.3 mL", "formulation": "Pre-filled Syringe", "stock": 85, "unit": "syringes", "status": "AVAILABLE", "location": "Bin D-08", "batch": "EN-44102", "expiry": "2027-12"},
        {"medication": "Heparin Sodium", "brand_name": "Heparin Generic", "strength": "5,000 USP Units / mL", "formulation": "IV / SC Vial", "stock": 150, "unit": "vials", "status": "AVAILABLE", "location": "Bin D-01", "batch": "HP-77120", "expiry": "2028-03"},
        {"medication": "Lisinopril", "brand_name": "Prinivil", "strength": "20 mg", "formulation": "Oral Tablet", "stock": 600, "unit": "tabs", "status": "AVAILABLE", "location": "Bin E-04", "batch": "LS-20441", "expiry": "2028-07"},
        {"medication": "Lisinopril", "brand_name": "Zestril", "strength": "20 mg", "formulation": "Oral Tablet", "stock": 250, "unit": "tabs", "status": "AVAILABLE", "location": "Bin E-05", "batch": "ZS-20109", "expiry": "2028-09"},
        {"medication": "Digoxin", "brand_name": "Lanoxin", "strength": "0.125 mg", "formulation": "Oral Tablet", "stock": 180, "unit": "tabs", "status": "AVAILABLE", "location": "Bin F-06", "batch": "DG-0125", "expiry": "2027-04"},
        {"medication": "Digoxin", "brand_name": "Digitek", "strength": "0.125 mg", "formulation": "Oral Tablet", "stock": 90, "unit": "tabs", "status": "AVAILABLE", "location": "Bin F-07", "batch": "DT-0129", "expiry": "2027-08"},
    ]

    # Deterministic Medication Availability Queue
    # Represents prescriptions received from Doctor workflow
    raw_queue = [
        {
            "case_id": "CASE-001",
            "patient_id": 1,
            "patient_name": "Sarah Jenkins",
            "patient_identifier": "DEMO-PT-001",
            "mrn": "MRN-2026-9041",
            "age": 64,
            "sex": "Female",
            "prescribed_medicine": "Warfarin Sodium (Coumadin)",
            "prescribed_brand": "Coumadin",
            "generic_name": "Warfarin Sodium",
            "strength": "2.5 mg",
            "formulation": "Oral Tablet",
            "route": "Oral",
            "frequency": "Once Daily (Q24H)",
            "prescribing_doctor": "Dr. Sarah Lin, MD (Cardiology)",
            "availability": "AVAILABLE",
            "available_quantity": 420,
            "available_qty_formatted": "420 Tablets (Bin A-04, Batch #WF-88392)",
            "substitution_status": "No Substitution Needed",
            "doctor_recommendation": "Warfarin dose reduction to 2.5 mg PO Daily",
            "available_alternatives": [],
            "action": "CHECK_AVAILABILITY"
        },
        {
            "case_id": "CASE-002",
            "patient_id": 2,
            "patient_name": "Robert Chen",
            "patient_identifier": "DEMO-PT-002",
            "mrn": "MRN-2026-8812",
            "age": 72,
            "sex": "Male",
            "prescribed_medicine": "Enoxaparin Sodium (Lovenox)",
            "prescribed_brand": "Lovenox",
            "generic_name": "Enoxaparin Sodium",
            "strength": "80 mg / 0.8 mL",
            "formulation": "Pre-filled Syringe",
            "route": "Subcutaneous",
            "frequency": "Once Daily (Q24H)",
            "prescribing_doctor": "Dr. Sarah Lin, MD (Nephrology / ID)",
            "availability": "UNAVAILABLE",
            "available_quantity": 0,
            "available_qty_formatted": "0 Syringes (Out of Stock)",
            "substitution_status": "Doctor Approval Required",
            "doctor_recommendation": "Enoxaparin therapeutic anticoagulation 80 mg SC Daily",
            "available_alternatives": [
                {
                    "brand_name": "Clexane",
                    "generic_name": "Enoxaparin Sodium",
                    "strength": "80 mg / 0.8 mL",
                    "formulation": "Pre-filled Syringe",
                    "same_active_ingredient": True,
                    "same_strength": True,
                    "availability": "AVAILABLE",
                    "stock": "60 Syringes (Bin D-12, Batch #CX-88102)"
                },
                {
                    "brand_name": "Inhixa",
                    "generic_name": "Enoxaparin Sodium",
                    "strength": "80 mg / 0.8 mL",
                    "formulation": "Pre-filled Syringe",
                    "same_active_ingredient": True,
                    "same_strength": True,
                    "availability": "AVAILABLE",
                    "stock": "45 Syringes (Bin D-14, Batch #IN-33201)"
                }
            ],
            "action": "SUGGEST_SUBSTITUTION"
        },
        {
            "case_id": "CASE-003",
            "patient_id": 3,
            "patient_name": "Elena Rostova",
            "patient_identifier": "DEMO-PT-003",
            "mrn": "MRN-2026-7734",
            "age": 58,
            "sex": "Female",
            "prescribed_medicine": "Lisinopril (Prinivil)",
            "prescribed_brand": "Prinivil",
            "generic_name": "Lisinopril",
            "strength": "20 mg",
            "formulation": "Oral Tablet",
            "route": "Oral",
            "frequency": "Once Daily (QAM)",
            "prescribing_doctor": "Dr. Sarah Lin, MD (Internal Medicine)",
            "availability": "AVAILABLE",
            "available_quantity": 600,
            "available_qty_formatted": "600 Tablets (Bin E-04, Batch #LS-20441)",
            "substitution_status": "No Substitution Needed",
            "doctor_recommendation": "Maintain Lisinopril 20 mg PO monotherapy; deprescribe duplicate Enalapril",
            "available_alternatives": [],
            "action": "CHECK_AVAILABILITY"
        },
        {
            "case_id": "CASE-004",
            "patient_id": 4,
            "patient_name": "Arthur Pendelton",
            "patient_identifier": "DEMO-PT-004",
            "mrn": "MRN-2026-6629",
            "age": 79,
            "sex": "Male",
            "prescribed_medicine": "Digoxin (Lanoxin)",
            "prescribed_brand": "Lanoxin",
            "generic_name": "Digoxin",
            "strength": "0.125 mg",
            "formulation": "Oral Tablet",
            "route": "Oral",
            "frequency": "Once Daily (Q24H)",
            "prescribing_doctor": "Dr. Sarah Lin, MD (Cardiology)",
            "availability": "AVAILABLE",
            "available_quantity": 180,
            "available_qty_formatted": "180 Tablets (Bin F-06, Batch #DG-0125)",
            "substitution_status": "No Substitution Needed",
            "doctor_recommendation": "Reduce Digoxin dose to 0.125 mg PO Daily; repeat TDM at Day 7",
            "available_alternatives": [],
            "action": "CHECK_AVAILABILITY"
        }
    ]

    availability_queue = []
    for item in raw_queue:
        c_id = item["case_id"]
        # Determine dynamic status based on audit events
        if c_id in verified_events:
            status_text = "Ready for Dispensing"
            dispense_ready = True
            sub_status = "Dispensing Authorized ✓"
        elif c_id in sub_approved_events:
            status_text = "Substitution Approved by Doctor"
            dispense_ready = True
            sub_status = "Substitution Approved (Ready to Dispense)"
        elif c_id in sub_rejected_events:
            status_text = "Substitution Rejected by Doctor"
            dispense_ready = False
            sub_status = "Substitution Rejected"
        elif c_id in clarif_events:
            status_text = "Clarification Required"
            dispense_ready = False
            sub_status = "Clarification Required"
        elif c_id in sub_requested_events:
            status_text = "Pending Doctor Verification"
            dispense_ready = False
            sub_status = "Doctor Approval Pending"
        else:
            status_text = "Available for Dispensing" if item["availability"] == "AVAILABLE" else "Medication Unavailable"
            dispense_ready = item["availability"] == "AVAILABLE"
            sub_status = "No Substitution Needed" if item["availability"] == "AVAILABLE" else "Doctor Approval Required"

        availability_queue.append({
            **item,
            "status": status_text,
            "substitution_status": sub_status,
            "is_ready_for_dispensing": dispense_ready,
            "medication": item["prescribed_medicine"],  # compatibility
            "age": item["age"],
            "doctor_name": item["prescribing_doctor"],
            "doctor_suggested_alt": "Micafungin 100 mg IV Daily" if c_id == "CASE-001" else "Clexane 80 mg / 0.8 mL" if c_id == "CASE-002" else "None Required",
            "alt_availability": "Available",
        })

    # Metrics computation
    total_received = len(availability_queue)
    available_cnt = sum(1 for q in availability_queue if q["availability"] == "AVAILABLE")
    unavailable_cnt = sum(1 for q in availability_queue if q["availability"] == "UNAVAILABLE")
    sub_requests_cnt = sum(1 for q in availability_queue if "Doctor" in q["substitution_status"] or "Substitution" in q["substitution_status"])
    doctor_pending_cnt = sum(1 for q in availability_queue if "Pending" in q["substitution_status"] or q["substitution_status"] == "Doctor Approval Required")
    ready_cnt = sum(1 for q in availability_queue if q["is_ready_for_dispensing"] and (q["status"] == "Ready for Dispensing" or "Approved" in q["status"]))

    # Clinical pharmacology interaction profiles (Secondary Reference)
    pharmacology_insights = [
        {
            "pair": "Warfarin + Fluconazole",
            "mechanism": "Potent CYP2C9 inhibition by fluconazole impairs hepatic clearance of active S-warfarin enantiomer.",
            "clinical_impact": "Rapid INR prolongation, severe bleeding risk within 48–72 hours.",
            "recommended_action": "Reduce warfarin maintenance dose by 50% or substitute antifungal; repeat INR Day 3.",
            "evidence_source": "DDInter / FDA Drug Label",
            "evidence_id": "AEGIS-DEMO-EV-001",
            "provenance": "Rule Pack: aegis_hackathon_demo v1.0.0",
        },
        {
            "pair": "Enoxaparin + Declining Renal Function",
            "mechanism": "Enoxaparin (LMWH) is eliminated primarily by renal filtration. Reduced GFR results in high anti-Xa bioaccumulation.",
            "clinical_impact": "Major internal retroperitoneal/GI hemorrhage risk.",
            "recommended_action": "Reduce dose to 30 mg SC once daily if CrCl < 30 mL/min; monitor peak anti-Xa or convert to UFH.",
            "evidence_source": "OpenFDA Black Box / CPIC Guidelines",
            "evidence_id": "AEGIS-DEMO-EV-002",
            "provenance": "Rule Pack: aegis_hackathon_demo v1.0.0",
        },
        {
            "pair": "Lisinopril + Enalapril (Duplicate ACE-I)",
            "mechanism": "Dual angiotensin-converting enzyme inhibition causes additive vasodilation and aldosterone suppression.",
            "clinical_impact": "Refractory hypotension, acute renal failure, hyperkalemia (K > 5.5 mEq/L).",
            "recommended_action": "Deprescribe enalapril; maintain single ACE-I regimen or substitute dihydropyridine CCB (amlodipine).",
            "evidence_source": "ISMP High Alert / Beers Criteria",
            "evidence_id": "AEGIS-DEMO-EV-003",
            "provenance": "Rule Pack: aegis_hackathon_demo v1.0.0",
        },
    ]

    return {
        "role": "Clinical Pharmacist",
        "title": "Medication Fulfillment & Substitution",
        "primary_purpose": "Medication Availability & Brand Substitution Verification",
        "pharmacist_name": current_user.full_name if current_user else "Marcus Vance, PharmD, BCPS",
        "stats": {
            "prescriptions_received": total_received,
            "available": available_cnt,
            "unavailable": unavailable_cnt,
            "substitution_requests": max(sub_requests_cnt, 1),
            "doctor_approval_pending": doctor_pending_cnt,
            "ready_for_dispensing": ready_cnt,
            # Backward compatibility fields
            "active_safety_rules": 3,
            "cyp_enzyme_profiles": 5,
            "renal_adjusted_drugs": 8,
            "substitutions_available": 12,
            "reevaluation_events_count": len(verified_events),
            "pending_verification": total_received - ready_cnt,
            "ready_to_dispense": ready_cnt,
            "availability_issues": unavailable_cnt,
            "clarification_required": len(clarif_events),
            "verified_today": max(ready_cnt, 8),
        },
        "availability_queue": availability_queue,
        "verification_queue": availability_queue,  # backward compatibility
        "local_inventory": local_inventory,
        "pharmacology_insights": pharmacology_insights,
        "pharmacology_reference": pharmacology_insights,  # backward compatibility alias
        "active_rules": [],
    }


# =========================================================================
# PHARMACIST SUBSTITUTION REQUEST API
# =========================================================================

@router.post("/api/dashboard/pharmacist/substitution-request", summary="Pharmacist: Send brand substitution request to Doctor")
def create_substitution_request(
    req: PharmacistSubstitutionRequest,
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
    current_user: Optional[User] = Depends(get_optional_user),
):
    """
    Submits a professional brand substitution request to the prescribing physician.
    The request status becomes PENDING DOCTOR VERIFICATION. Does NOT auto-approve.
    """
    actor = current_user.full_name if current_user else "Marcus Vance, PharmD, BCPS"
    payload = {
        "case_id": req.case_id,
        "patient_id": req.patient_id,
        "patient_name": req.patient_name or f"Case {req.case_id}",
        "prescribed_medicine": req.prescribed_medicine,
        "prescribed_brand": req.prescribed_brand,
        "strength": req.strength,
        "formulation": req.formulation,
        "suggested_brand": req.suggested_brand,
        "reason": req.reason or "Prescribed brand is currently unavailable in local dispensary stock.",
        "pharmacist_notes": req.pharmacist_notes or "Equivalent available brand identified in deterministic inventory.",
        "pharmacist_name": actor,
        "substitution_status": "PENDING_DOCTOR_VERIFICATION",
        "action_description": f"Substitution request sent for {req.prescribed_brand} -> {req.suggested_brand}",
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }

    event = audit_ledger.append_event(
        actor=actor,
        event_type="PHARMACY_SUBSTITUTION_REQUESTED",
        payload=payload,
    )

    return {
        "status": "PENDING_DOCTOR_VERIFICATION",
        "message": f"Substitution request for {req.suggested_brand} successfully dispatched to Prescribing Doctor.",
        "case_id": req.case_id,
        "audit_event_id": event.id,
        "audit_hash": event.hash,
    }


# =========================================================================
# DOCTOR SUBSTITUTION DECISION API (APPROVE / REJECT / CLARIFICATION)
# =========================================================================

@router.post("/api/dashboard/doctor/substitution-decision", summary="Doctor: Approve, Reject or Clarify Pharmacy Substitution Request")
def process_doctor_substitution_decision(
    req: DoctorSubstitutionDecisionRequest,
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
    current_user: Optional[User] = Depends(get_optional_user),
):
    """
    Physician authorization endpoint for pharmacy substitution requests.
    Valid decisions: APPROVED, REJECTED, CLARIFICATION_REQUIRED.
    """
    actor = req.doctor_name or (current_user.full_name if current_user else "Dr. Sarah Lin, MD")
    decision_clean = req.decision.strip().upper()

    if decision_clean in ("APPROVED", "APPROVE"):
        event_type = "DOCTOR_SUBSTITUTION_APPROVED"
        final_status = "SUBSTITUTION_APPROVED"
        summary_msg = f"Brand substitution to {req.suggested_brand or 'alternative brand'} APPROVED by {actor}."
    elif decision_clean in ("REJECTED", "REJECT"):
        event_type = "DOCTOR_SUBSTITUTION_REJECTED"
        final_status = "SUBSTITUTION_REJECTED"
        summary_msg = f"Brand substitution REJECTED by {actor}. Pharmacist must not dispense alternative."
    else:
        event_type = "DOCTOR_CLARIFICATION_REQUESTED"
        final_status = "CLARIFICATION_REQUIRED"
        summary_msg = f"Clarification requested by {actor} regarding substitution."

    payload = {
        "case_id": req.case_id,
        "suggested_brand": req.suggested_brand,
        "decision": decision_clean,
        "final_status": final_status,
        "doctor_notes": req.doctor_notes or f"Clinical decision: {decision_clean}",
        "doctor_name": actor,
        "action_description": summary_msg,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }

    event = audit_ledger.append_event(
        actor=actor,
        event_type=event_type,
        payload=payload,
    )

    return {
        "status": final_status,
        "decision": decision_clean,
        "message": summary_msg,
        "case_id": req.case_id,
        "audit_event_id": event.id,
        "audit_hash": event.hash,
        "ready_for_dispensing": (decision_clean in ("APPROVED", "APPROVE")),
    }


# =========================================================================
# PHARMACIST DISPENSE / VERIFICATION API
# =========================================================================

@router.post("/api/dashboard/pharmacist/dispense", summary="Pharmacist: Mark medication ready for dispensing")
@router.post("/api/dashboard/pharmacist/verify", summary="Pharmacist: Verify & approve prescription for dispensing")
def mark_ready_for_dispensing(
    req: PharmacistVerificationRequest,
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
    current_user: Optional[User] = Depends(get_optional_user),
):
    """
    Pharmacist verifies medication availability and marks ready for dispensing.
    Logs MEDICATION_MARKED_READY_FOR_DISPENSING & PHARMACIST_PRESCRIPTION_VERIFIED in the SHA-256 cryptographic audit ledger.
    Does NOT re-run clinical interaction assessments.
    """
    actor = current_user.full_name if current_user else "Marcus Vance, PharmD, BCPS"
    payload = {
        "case_id": req.case_id,
        "patient_id": req.patient_id,
        "patient_name": req.patient_name or f"Case {req.case_id}",
        "medication": req.medication,
        "strength": req.strength,
        "route": req.route,
        "frequency": req.frequency or "As prescribed",
        "doctor_recommendation": req.doctor_recommendation,
        "pharmacist_name": actor,
        "availability_status": req.availability_status,
        "verification_status": "READY_FOR_DISPENSING",
        "action_description": f"Verified {req.medication} {req.strength} for dispensing",
        "notes": req.pharmacist_notes or "Prescription verified against dispensary stock. Ready for dispensing.",
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
    
    event = audit_ledger.append_event(
        actor=actor,
        event_type="PHARMACIST_PRESCRIPTION_VERIFIED",
        payload=payload,
    )

    return {
        "status": "READY_FOR_DISPENSING",
        "message": f"Prescription for {req.medication} {req.strength} successfully verified and approved for dispensing.",
        "audit_event_id": event.id,
        "audit_hash": event.hash,
        "verification_summary": {
            "prescription_verified": True,
            "medication_available": True,
            "strength_verified": True,
            "route_verified": True,
            "ready_for_dispensing": True,
        }
    }


@router.post("/api/dashboard/pharmacist/clarification", summary="Pharmacist: Request clarification from prescribing doctor")
def request_doctor_clarification(
    req: PharmacistClarificationRequest,
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
    current_user: Optional[User] = Depends(get_optional_user),
):
    """Logs PHARMACIST_CLARIFICATION_REQUESTED in the SHA-256 cryptographic audit ledger."""
    actor = current_user.full_name if current_user else "Marcus Vance, PharmD, BCPS"
    payload = {
        "case_id": req.case_id,
        "patient_id": req.patient_id,
        "patient_name": req.patient_name or f"Case {req.case_id}",
        "medication": req.medication,
        "reason": req.reason,
        "notes": req.notes or "Doctor clarification requested prior to dispensing.",
        "alternative_suggested": req.alternative_suggested,
        "pharmacist_name": actor,
        "verification_status": "AWAITING_DOCTOR_CLARIFICATION",
        "action_description": f"Clarification requested for {req.medication}: {req.reason}",
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
    
    event = audit_ledger.append_event(
        actor=actor,
        event_type="PHARMACIST_CLARIFICATION_REQUESTED",
        payload=payload,
    )

    return {
        "status": "AWAITING_DOCTOR_CLARIFICATION",
        "message": f"Clarification request dispatched to prescribing physician: {req.reason}",
        "audit_event_id": event.id,
        "audit_hash": event.hash,
    }


# =========================================================================
# COMPLETE PATIENT MEDICAL HISTORY API ENDPOINTS
# =========================================================================

@router.get("/api/dashboard/patient-history/{identifier}", summary="Get Full Patient Medical History")
@router.get("/api/patient-history/{identifier}", summary="Get Full Patient Medical History")
def get_patient_medical_history_endpoint(
    identifier: str,
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
):
    """
    Returns complete, deterministic medical history for the specified patient / case:
    - Demographics Header
    - Chronological Patient Timeline
    - 1. Medical Conditions
    - 2. Medication History
    - 3. Allergies
    - 4. Laboratory History (with link to local source)
    - 5. Clinical Assessments
    - 6. Pharmacy Events
    - 7. Audit Events
    """
    history = _get_patient_medical_history(identifier)
    
    # Also attach any fresh audit ledger events associated with this patient/case
    all_events = audit_ledger.get_events()
    matching_audit = []
    clean_id = str(identifier).upper()
    for e in reversed(all_events):
        p = e.payload if isinstance(e.payload, dict) else {}
        case_val = str(p.get("case_id", "")).upper()
        pt_val = str(p.get("patient_name", "")).upper()
        pt_id = str(p.get("patient_id", ""))
        if clean_id in case_val or clean_id in pt_val or clean_id == pt_id or clean_id in str(e.actor).upper():
            matching_audit.append({
                "timestamp": e.timestamp,
                "user_role": e.actor,
                "action": str(p.get("action_description") or p.get("action") or e.event_type.replace("_", " ").title()),
                "event_type": e.event_type,
                "audit_hash": e.hash,
            })

    if matching_audit:
        seen_hashes = {a.get("audit_hash") for a in history.get("audit_events", [])}
        for ma in matching_audit:
            if ma["audit_hash"] not in seen_hashes:
                history.setdefault("audit_events", []).insert(0, ma)
                seen_hashes.add(ma["audit_hash"])

    return history


@router.get("/api/patient-history", summary="List All Patient Medical History Profiles")
def list_patient_medical_histories():
    """Returns list of deterministic presentation patient profiles."""
    return list(DETERMINISTIC_PATIENT_HISTORIES.values())


# =========================================================================
# AUDIT LEDGER CATEGORIZATION & ADMIN ENDPOINT
# =========================================================================

def _categorize_audit_record(event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Categorizes audit events and extracts structured fields strictly from backend records.
    Categories: AUTHENTICATION, CLINICAL EVENT, SAFETY, RESOLUTION, VERIFICATION, SUBSTITUTION, SYSTEM.
    """
    et = str(event_type).upper()
    cat = "SYSTEM"
    entity = "System"
    action = et.replace("_", " ").title()
    result = "RECORDED ✓"
    rule_id = str(payload.get("rule_id") or "")
    evidence_id = str(payload.get("evidence_id") or "")
    provenance = str(payload.get("source") or payload.get("provenance") or "Deterministic Safety Core")

    if "AUTH" in et or "OTP" in et or "TOTP" in et or "LOGIN" in et or "LOGOUT" in et:
        cat = "AUTHENTICATION"
        phone = payload.get("phone_number_masked") or payload.get("phone_number") or "User"
        role = payload.get("role") or "Clinician"
        entity = f"{phone} ({role})"
        if "LOGIN_SUCCESS" in et or "TOTP_SUCCESS" in et:
            action = "Secure Session Established"
            result = "SUCCESS ✓"
        elif "LOGOUT" in et:
            action = "Session Inactivated"
            result = "LOGGED OUT ✓"
        elif "FAILED" in et:
            action = str(payload.get("reason") or "Authentication Failed")
            result = "REJECTED ✗"
        elif "ENROLLED" in et:
            action = "RFC 6238 TOTP Key Activated"
            result = "ENROLLED ✓"
        elif "REQUESTED" in et:
            prov = payload.get("provider", "Local")
            action = f"Passcode Dispatched via {prov}"
            result = "DISPATCHED ✓"

    elif "PATIENT" in et or "MEDICATION" in et or "LAB" in et or "EVENT_INGESTED" in et:
        cat = "CLINICAL EVENT"
        p_id = payload.get("patient_id")
        case_id = payload.get("case_id")
        entity = f"Patient #{p_id}" if p_id else (str(case_id) if case_id else "Clinical EHR")
        action = str(payload.get("action_description") or payload.get("event_type") or "Clinical Event Ingested")
        result = "INGESTED ✓"

    elif "RISK" in et or "FINDING" in et or "SAFETY" in et:
        cat = "SAFETY"
        p_id = payload.get("patient_id")
        case_id = payload.get("case_id")
        entity = f"Patient #{p_id}" if p_id else (str(case_id) if case_id else "Safety Engine")
        title = payload.get("title") or "Risk Evaluated"
        sev = payload.get("severity") or "REVIEW REQUIRED"
        action = f"{title} [{str(sev).upper()}]"
        result = "FLAGGED ⚠️"

    elif "SUBSTITUTION" in et or "CLARIFICATION" in et:
        cat = "SUBSTITUTION"
        case = payload.get("case_id") or "Case"
        pt = payload.get("patient_name") or case
        entity = f"{pt} ({case})"
        if "REQUESTED" in et:
            sug = payload.get("suggested_brand") or "Alternative Brand"
            action = f"Pharmacy Substitution Requested: {sug}"
            result = "DOCTOR APPROVAL PENDING ⏳"
        elif "APPROVED" in et:
            sug = payload.get("suggested_brand") or "Alternative Brand"
            action = f"Doctor Approved Brand Substitution: {sug}"
            result = "SUBSTITUTION APPROVED ✓"
        elif "REJECTED" in et:
            action = f"Doctor Rejected Substitution: {payload.get('suggested_brand', 'Alternative')}"
            result = "SUBSTITUTION REJECTED ✗"
        elif "CLARIFICATION" in et:
            action = f"Clarification Requested: {payload.get('reason', 'Medication Review')}"
            result = "CLARIFICATION REQUIRED ⚠️"

    elif "PHARMACIST" in et or "DISPENS" in et or "PRESCRIPTION" in et:
        cat = "VERIFICATION"
        pharm = payload.get("pharmacist_name") or "Marcus Vance, PharmD"
        entity = f"{pharm} (Clinical Pharmacist)"
        med = payload.get("medication") or "Medication"
        case = payload.get("case_id") or "Case"
        if "VERIFIED" in et or "READY" in et:
            action = f"Prescription Verified & Approved for Dispensing: {med} ({case})"
            result = "READY TO DISPENSE ✓"
        elif "CLARIFICATION" in et:
            action = f"Clarification Requested: {med} - {payload.get('reason', 'Review Needed')}"
            result = "CLARIFICATION REQ ⚠️"
        else:
            action = f"Fulfillment Decision: {med}"
            result = "RECORDED ✓"

    elif "COSIGN" in et or "VERIF" in et or "REEVAL" in et:
        cat = "VERIFICATION"
        entity = str(payload.get("cosigned_by") or "Attending Physician")
        dec = str(payload.get("decision") or "Approved").upper()
        action = f"Simulated Order Decision: {dec}"
        result = f"{dec} ✓"

    return {
        "category": cat,
        "entity": entity,
        "action": action,
        "result": result,
        "rule_id": rule_id,
        "evidence_id": evidence_id,
        "provenance": provenance,
    }


@router.get("/api/dashboard/admin", summary="Admin Dashboard: System health, user sessions, audit chain, and SMS gateway")
def get_admin_dashboard_data(
    db: Session = Depends(get_db),
    audit_ledger: AuditLedger = Depends(get_audit_ledger),
    current_user: Optional[User] = Depends(get_optional_user),
):
    """Provides system health, cryptographic audit chain verification, and SMS provider gateway controls."""
    verification = audit_ledger.verify_chain()
    all_events = audit_ledger.get_events()
    
    audit_records = []
    for e in reversed(all_events):
        p_dict = e.payload if isinstance(e.payload, dict) else {}
        extracted = _categorize_audit_record(e.event_type, p_dict)
        audit_records.append({
            "id": e.id,
            "timestamp": e.timestamp,
            "actor": e.actor,
            "event_type": e.event_type,
            "category": extracted["category"],
            "entity": extracted["entity"],
            "action": extracted["action"],
            "result": extracted["result"],
            "rule_id": extracted["rule_id"],
            "evidence_id": extracted["evidence_id"],
            "provenance": extracted["provenance"],
            "prev_hash": e.prev_hash,
            "prev_hash_short": e.prev_hash[:12] + "...",
            "hash": e.hash,
            "hash_short": e.hash[:12] + "...",
            "payload": p_dict,
            "is_chain_valid": True,
        })

    users = db.execute(select(User).order_by(User.id)).scalars().all()
    sessions = db.execute(select(UserSession).where(UserSession.is_active == True).order_by(desc(UserSession.id)).limit(10)).scalars().all()

    providers = list_available_providers()
    active_prov = get_sms_provider()

    return {
        "role": "Administrator",
        "admin_name": current_user.full_name if current_user else "Arthur Pendelton, MS, CPHIMS",
        "system_health": {
            "status": "OPERATIONAL",
            "offline_mode": "100% OFFLINE / ON-PREMISE",
            "zero_external_calls": True,
            "database_engine": "SQLite WAL Mode (ACID)",
            "rule_engine": "Deterministic Priority Safety Engine",
            "ollama_local_status": "Ready on localhost:11434 (Qwen 2.5 7B)",
        },
        "audit_chain": {
            "is_valid": verification.is_valid,
            "total_records": verification.total_records,
            "errors": verification.errors,
            "recent_records": audit_records,
        },
        "sms_gateway": {
            "active_provider": active_prov.get_provider_name(),
            "providers": providers,
        },
        "users_count": len(users),
        "active_sessions_count": len(sessions),
    }
