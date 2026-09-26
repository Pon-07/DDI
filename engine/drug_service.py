import re
from typing import Any, Dict, List, Optional, Set, Tuple
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from agents.knowledge.normalizer import DrugNormalizer
from models import Drug, InteractionEvidence, SafetyRule

# Canonical local formulary / knowledge base catalog for offline clinical safety
LOCAL_MEDICATION_CATALOG: List[Dict[str, Any]] = [
    {
        "drug_name": "Warfarin",
        "normalized_name": "warfarin",
        "aliases": ["warfarin sodium", "coumadin", "jantoven"],
        "rxnorm_code": "11289",
        "category": "Anticoagulant (Vitamin K Antagonist)",
        "source": "OpenFDA / DDInter / RxNorm",
        "description": "Oral anticoagulant indicated for prophylaxis and treatment of venous thromboembolism and thromboembolic complications.",
    },
    {
        "drug_name": "Fluconazole",
        "normalized_name": "fluconazole",
        "aliases": ["diflucan"],
        "rxnorm_code": "4450",
        "category": "Antifungal (Triazole / CYP2C9 & CYP3A4 Inhibitor)",
        "source": "OpenFDA / DDInter / RxNorm",
        "description": "Triazole antifungal agent that potent inhibits CYP2C9, significantly impairing S-warfarin clearance.",
    },
    {
        "drug_name": "Enoxaparin",
        "normalized_name": "enoxaparin",
        "aliases": ["enoxaparin sodium", "lovenox"],
        "rxnorm_code": "67108",
        "category": "Anticoagulant (Low Molecular Weight Heparin)",
        "source": "OpenFDA / DDInter / RxNorm",
        "description": "Low-molecular-weight heparin (LMWH) eliminated by renal filtration; requires dose adjustment in renal impairment.",
    },
    {
        "drug_name": "Lisinopril",
        "normalized_name": "lisinopril",
        "aliases": ["zestril", "prinivil", "lisinopril dihydrate"],
        "rxnorm_code": "29046",
        "category": "Antihypertensive (ACE Inhibitor)",
        "source": "OpenFDA / DDInter / RxNorm",
        "description": "Angiotensin-converting enzyme (ACE) inhibitor for hypertension and heart failure management.",
    },
    {
        "drug_name": "Enalapril",
        "normalized_name": "enalapril",
        "aliases": ["enalapril maleate", "vasotec"],
        "rxnorm_code": "3827",
        "category": "Antihypertensive (ACE Inhibitor)",
        "source": "OpenFDA / DDInter / RxNorm",
        "description": "ACE inhibitor prodrug converted to enalaprilat; dual ACE-I therapy causes refractory hypotension and acute renal injury.",
    },
    {
        "drug_name": "Aspirin",
        "normalized_name": "aspirin",
        "aliases": ["acetylsalicylic acid", "asa", "ecotrin", "bayer aspirin"],
        "rxnorm_code": "1191",
        "category": "Antiplatelet / NSAID (COX Inhibitor)",
        "source": "OpenFDA / DDInter / RxNorm",
        "description": "Irreversible cyclooxygenase-1 (COX-1) inhibitor indicated for antiplatelet aggregation and cardiovascular event reduction.",
    },
    {
        "drug_name": "Metoprolol",
        "normalized_name": "metoprolol",
        "aliases": ["metoprolol succinate", "metoprolol tartrate", "lopressor", "toprol-xl"],
        "rxnorm_code": "6918",
        "category": "Beta-Blocker (Cardioselective Beta-1 Adrenergic Blocker)",
        "source": "OpenFDA / DDInter / RxNorm",
        "description": "Cardioselective beta-1 adrenergic receptor blocker indicated for hypertension, angina pectoris, and heart failure.",
    },
    {
        "drug_name": "Potassium Chloride",
        "normalized_name": "potassium chloride",
        "aliases": ["kcl", "k-dur", "micro-k", "klor-con"],
        "rxnorm_code": "8591",
        "category": "Electrolyte Supplement",
        "source": "OpenFDA / DDInter / RxNorm",
        "description": "Major intracellular cation supplement; concomitant use with ACE inhibitors increases risk of severe hyperkalemia.",
    },
    {
        "drug_name": "Ciprofloxacin",
        "normalized_name": "ciprofloxacin",
        "aliases": ["ciprofloxacin hydrochloride", "cipro", "cipro xr"],
        "rxnorm_code": "2551",
        "category": "Fluoroquinolone Antibacterial / CYP1A2 Inhibitor",
        "source": "OpenFDA / DDInter / RxNorm",
        "description": "Broad-spectrum fluoroquinolone antibiotic and moderate-to-strong inhibitor of CYP1A2.",
    },
    {
        "drug_name": "Theophylline",
        "normalized_name": "theophylline",
        "aliases": ["theo-24", "uniphyl", "theochron"],
        "rxnorm_code": "10438",
        "category": "Methylxanthine Bronchodilator (CYP1A2 Substrate)",
        "source": "OpenFDA / DDInter / RxNorm",
        "description": "Methylxanthine phosphodiesterase inhibitor with narrow therapeutic index; CYP1A2 inhibition elevates levels to toxic ranges.",
    },
    {
        "drug_name": "Simvastatin",
        "normalized_name": "simvastatin",
        "aliases": ["zocor"],
        "rxnorm_code": "36567",
        "category": "HMG-CoA Reductase Inhibitor (CYP3A4 Substrate)",
        "source": "OpenFDA / DDInter / RxNorm",
        "description": "Statin lipid-lowering agent metabolized by CYP3A4; strong CYP3A4 inhibitors cause severe rhabdomyolysis.",
    },
    {
        "drug_name": "Amiodarone",
        "normalized_name": "amiodarone",
        "aliases": ["amiodarone hydrochloride", "cordarone", "pacerone"],
        "rxnorm_code": "703",
        "category": "Antiarrhythmic (Class III / Multi-CYP Inhibitor)",
        "source": "OpenFDA / DDInter / RxNorm",
        "description": "Class III antiarrhythmic agent and potent inhibitor of CYP2C9, CYP3A4, and P-glycoprotein.",
    },
    {
        "drug_name": "Digoxin",
        "normalized_name": "digoxin",
        "aliases": ["lanoxin", "digitek"],
        "rxnorm_code": "3407",
        "category": "Cardiac Glycoside (P-gp Substrate)",
        "source": "OpenFDA / DDInter / RxNorm",
        "description": "Cardiac glycoside with narrow therapeutic range; P-glycoprotein inhibitors increase serum digoxin concentrations.",
    },
    {
        "drug_name": "Levothyroxine",
        "normalized_name": "levothyroxine",
        "aliases": ["synthroid", "levoxyl", "tirosint"],
        "rxnorm_code": "10582",
        "category": "Thyroid Hormone",
        "source": "OpenFDA / DDInter / RxNorm",
        "description": "Synthetic T4 thyroid hormone replacement therapy.",
    },
    {
        "drug_name": "Atorvastatin",
        "normalized_name": "atorvastatin",
        "aliases": ["atorvastatin calcium", "lipitor"],
        "rxnorm_code": "83367",
        "category": "HMG-CoA Reductase Inhibitor (Statin)",
        "source": "OpenFDA / DDInter / RxNorm",
        "description": "Lipid-lowering statin indicated to prevent cardiovascular events.",
    },
    {
        "drug_name": "Clopidogrel",
        "normalized_name": "clopidogrel",
        "aliases": ["clopidogrel bisulfate", "plavix"],
        "rxnorm_code": "32968",
        "category": "Antiplatelet (P2Y12 Inhibitor)",
        "source": "OpenFDA / DDInter / RxNorm",
        "description": "Thienopyridine P2Y12 platelet inhibitor; prodrug activated by CYP2C19.",
    },
    {
        "drug_name": "Heparin",
        "normalized_name": "heparin",
        "aliases": ["heparin sodium", "unfractionated heparin", "ufh"],
        "rxnorm_code": "5224",
        "category": "Anticoagulant (Unfractionated Heparin)",
        "source": "OpenFDA / DDInter / RxNorm",
        "description": "Parenteral anticoagulant binding antithrombin III to inactivate thrombin and factor Xa.",
    },
    {
        "drug_name": "Furosemide",
        "normalized_name": "furosemide",
        "aliases": ["lasix"],
        "rxnorm_code": "4603",
        "category": "Loop Diuretic",
        "source": "OpenFDA / DDInter / RxNorm",
        "description": "Loop diuretic inhibiting Na-K-2Cl symporters in the thick ascending limb of Henle.",
    },
    {
        "drug_name": "Hydrochlorothiazide",
        "normalized_name": "hydrochlorothiazide",
        "aliases": ["hctz", "microzide"],
        "rxnorm_code": "5487",
        "category": "Thiazide Diuretic",
        "source": "OpenFDA / DDInter / RxNorm",
        "description": "Thiazide diuretic promoting renal excretion of sodium, chloride, and water.",
    },
    {
        "drug_name": "Omeprazole",
        "normalized_name": "omeprazole",
        "aliases": ["prilosec"],
        "rxnorm_code": "7646",
        "category": "Proton Pump Inhibitor (CYP2C19 Inhibitor)",
        "source": "OpenFDA / DDInter / RxNorm",
        "description": "Proton pump inhibitor; competitive inhibitor of CYP2C19 affecting clopidogrel bioactivation.",
    },
    {
        "drug_name": "Pantoprazole",
        "normalized_name": "pantoprazole",
        "aliases": ["pantoprazole sodium", "protonix"],
        "rxnorm_code": "40790",
        "category": "Proton Pump Inhibitor",
        "source": "OpenFDA / DDInter / RxNorm",
        "description": "Proton pump inhibitor with lower CYP2C19 interaction potential.",
    },
    {
        "drug_name": "Metformin",
        "normalized_name": "metformin",
        "aliases": ["metformin hydrochloride", "glucophage"],
        "rxnorm_code": "6809",
        "category": "Biguanide Antidiabetic",
        "source": "OpenFDA / DDInter / RxNorm",
        "description": "Oral biguanide for type 2 diabetes mellitus; renal monitoring required to prevent lactic acidosis.",
    },
    {
        "drug_name": "Insulin",
        "normalized_name": "insulin",
        "aliases": ["insulin glargine", "lantus", "humalog", "novolog"],
        "rxnorm_code": "5856",
        "category": "Antidiabetic Hormone",
        "source": "OpenFDA / DDInter / RxNorm",
        "description": "Exogenous insulin indicated for glycemic control in diabetes mellitus.",
    },
    {
        "drug_name": "Spironolactone",
        "normalized_name": "spironolactone",
        "aliases": ["aldactone"],
        "rxnorm_code": "9997",
        "category": "Aldosterone Antagonist / Potassium-Sparing Diuretic",
        "source": "OpenFDA / DDInter / RxNorm",
        "description": "Aldosterone antagonist; potassium-sparing diuretic for heart failure and resistant hypertension.",
    },
    {
        "drug_name": "Amlodipine",
        "normalized_name": "amlodipine",
        "aliases": ["amlodipine besylate", "norvasc"],
        "rxnorm_code": "17767",
        "category": "Dihydropyridine Calcium Channel Blocker",
        "source": "OpenFDA / DDInter / RxNorm",
        "description": "Dihydropyridine calcium channel blocker for hypertension and coronary artery disease.",
    },
    {
        "drug_name": "Losartan",
        "normalized_name": "losartan",
        "aliases": ["losartan potassium", "cozaar"],
        "rxnorm_code": "52175",
        "category": "Angiotensin II Receptor Blocker (ARB)",
        "source": "OpenFDA / DDInter / RxNorm",
        "description": "Angiotensin II receptor antagonist indicated for hypertension and diabetic nephropathy.",
    },
]

_shared_normalizer = DrugNormalizer()


def normalize_medication_name(raw_name: str) -> str:
    """
    Cleans and standardizes medication string:
    - Strips leading/trailing whitespace
    - Case-normalizes
    - Removes common dosage annotations (e.g., '100mg', '5 mg tab')
    - Strips chemical salts (e.g., 'Sodium', 'HCl', 'Maleate')
    """
    if not raw_name or not isinstance(raw_name, str):
        return ""

    cleaned = raw_name.strip()
    # Remove trailing dose patterns like ' 100 mg', ' 5mg', ' 20 mg oral'
    cleaned = re.sub(r"\s+\d+(\.\d+)?\s*(mg|mcg|g|ml|units?|iu|meq)(\b.*)?$", "", cleaned, flags=re.IGNORECASE)
    normalized = _shared_normalizer.normalize(cleaned)
    return normalized.strip()


def is_medication_recognized(db: Optional[Session], raw_name: str) -> bool:
    """
    Deterministic check whether a medication exists in the local offline medication knowledge base.
    Validates against:
    1. Built-in Local Medication Catalog
    2. SQLite `drugs` table
    3. SQLite `interaction_evidence` / `interaction_rules`
    4. SQLite `safety_rules`
    Does NOT make any internet or external API calls.
    """
    if not raw_name or not isinstance(raw_name, str):
        return False

    clean_raw = raw_name.strip().lower()
    if not clean_raw:
        return False

    norm_name = normalize_medication_name(raw_name).lower()
    if not norm_name:
        return False

    # 1. Check local canonical catalog
    for item in LOCAL_MEDICATION_CATALOG:
        if clean_raw == item["drug_name"].lower() or norm_name == item["normalized_name"].lower():
            return True
        for alias in item.get("aliases", []):
            if clean_raw == alias.lower() or norm_name == _shared_normalizer.normalize(alias).lower():
                return True

    # 2. Check Database tables if DB session is provided
    if db:
        try:
            # Check `drugs` table
            db_drug = db.execute(
                select(Drug).where(
                    or_(
                        func.lower(Drug.drug_name) == clean_raw,
                        func.lower(Drug.normalized_name) == norm_name,
                        func.lower(Drug.drug_name) == norm_name,
                    )
                )
            ).scalar_one_or_none()
            if db_drug:
                return True

            # Check `safety_rules` table
            db_rule = db.execute(
                select(SafetyRule).where(
                    or_(
                        func.lower(SafetyRule.drug_a) == clean_raw,
                        func.lower(SafetyRule.drug_b) == clean_raw,
                        func.lower(SafetyRule.drug_a) == norm_name,
                        func.lower(SafetyRule.drug_b) == norm_name,
                    )
                )
            ).first()
            if db_rule:
                return True

            # Check `interaction_evidence` table
            db_ie = db.execute(
                select(InteractionEvidence).where(
                    or_(
                        func.lower(InteractionEvidence.drug_a) == clean_raw,
                        func.lower(InteractionEvidence.drug_b) == clean_raw,
                        func.lower(InteractionEvidence.drug_a) == norm_name,
                        func.lower(InteractionEvidence.drug_b) == norm_name,
                    )
                )
            ).first()
            if db_ie:
                return True
        except Exception:
            # Fallback gracefully to catalog
            pass

    return False


def get_medication_details(db: Optional[Session], raw_name: str) -> Optional[Dict[str, Any]]:
    """Retrieve full clinical metadata for a recognized local medication."""
    if not is_medication_recognized(db, raw_name):
        return None

    clean_raw = raw_name.strip().lower()
    norm_name = normalize_medication_name(raw_name).lower()

    for item in LOCAL_MEDICATION_CATALOG:
        if clean_raw == item["drug_name"].lower() or norm_name == item["normalized_name"].lower():
            return {
                "drug_name": item["drug_name"],
                "normalized_name": item["normalized_name"],
                "rxnorm_code": item["rxnorm_code"],
                "category": item["category"],
                "source": item["source"],
                "description": item["description"],
            }
        for alias in item.get("aliases", []):
            if clean_raw == alias.lower() or norm_name == _shared_normalizer.normalize(alias).lower():
                return {
                    "drug_name": item["drug_name"],
                    "normalized_name": item["normalized_name"],
                    "rxnorm_code": item["rxnorm_code"],
                    "category": item["category"],
                    "source": item["source"],
                    "description": item["description"],
                }

    # If in DB
    if db:
        try:
            db_drug = db.execute(
                select(Drug).where(
                    or_(
                        func.lower(Drug.drug_name) == clean_raw,
                        func.lower(Drug.normalized_name) == norm_name,
                    )
                )
            ).scalar_one_or_none()
            if db_drug:
                return {
                    "drug_name": db_drug.drug_name,
                    "normalized_name": db_drug.normalized_name,
                    "rxnorm_code": db_drug.rxnorm_code or "UNKNOWN",
                    "category": "Prescription Medication",
                    "source": db_drug.source or "Local Database",
                    "description": f"Verified local medication entity ({db_drug.drug_name}).",
                }
        except Exception:
            pass

    # Fallback to normalized presentation
    return {
        "drug_name": raw_name.strip().capitalize(),
        "normalized_name": norm_name,
        "rxnorm_code": "LOCAL",
        "category": "Clinical Agent",
        "source": "MICROMEDX Local Knowledge Base",
        "description": "Recognized clinical medication entity.",
    }


def search_local_medications(
    db: Optional[Session],
    query: str,
    limit: int = 12,
) -> List[Dict[str, Any]]:
    """
    Search medications matching a user search string against the local knowledge base.
    Runs 100% locally with zero external network requests.
    """
    if not query or not isinstance(query, str):
        return []

    q_clean = query.strip().lower()
    if not q_clean:
        return []

    q_norm = normalize_medication_name(query).lower()
    matches: List[Dict[str, Any]] = []
    seen_names: Set[str] = set()

    # 1. Search local catalog
    for item in LOCAL_MEDICATION_CATALOG:
        d_name_lower = item["drug_name"].lower()
        d_norm_lower = item["normalized_name"].lower()
        aliases_lower = [a.lower() for a in item.get("aliases", [])]

        is_match = (
            q_clean in d_name_lower
            or q_norm in d_norm_lower
            or any(q_clean in a for a in aliases_lower)
            or d_name_lower.startswith(q_clean)
            or d_norm_lower.startswith(q_norm)
        )

        if is_match and item["drug_name"] not in seen_names:
            seen_names.add(item["drug_name"])
            matches.append({
                "drug_name": item["drug_name"],
                "normalized_name": item["normalized_name"],
                "rxnorm_code": item["rxnorm_code"],
                "category": item["category"],
                "source": item["source"],
            })
            if len(matches) >= limit:
                return matches

    # 2. Search SQLite `drugs` table
    if db:
        try:
            db_drugs = db.execute(
                select(Drug).where(
                    or_(
                        func.lower(Drug.drug_name).like(f"%{q_clean}%"),
                        func.lower(Drug.normalized_name).like(f"%{q_norm}%"),
                    )
                ).limit(limit)
            ).scalars().all()

            for d in db_drugs:
                if d.drug_name not in seen_names:
                    seen_names.add(d.drug_name)
                    matches.append({
                        "drug_name": d.drug_name,
                        "normalized_name": d.normalized_name,
                        "rxnorm_code": d.rxnorm_code or "UNKNOWN",
                        "category": "Formulary Agent",
                        "source": d.source or "Local SQLite",
                    })
                    if len(matches) >= limit:
                        return matches
        except Exception:
            pass

    return matches


def validate_medication_input(
    db: Optional[Session],
    raw_name: str,
) -> Dict[str, Any]:
    """
    Validates drug name input:
    - Normalizes string
    - Checks offline knowledge base
    - If valid: returns structured clinical metadata
    - If invalid: returns structured rejection payload with guidance
    """
    norm = normalize_medication_name(raw_name)
    recognized = is_medication_recognized(db, raw_name)

    if not recognized:
        return {
            "is_valid": False,
            "status": "NOT_FOUND_IN_LOCAL_KNOWLEDGE_BASE",
            "message": f"Medication not recognized in the local medication knowledge base.",
            "raw_input": raw_name,
            "normalized_input": norm,
            "possible_actions": [
                "Check medication spelling (e.g. Warfarin, Fluconazole, Enoxaparin, Lisinopril)",
                "Search supported medication names in local knowledge base",
                "Review available local knowledge sources (OpenFDA / DDInter)",
            ],
            "details": None,
        }

    details = get_medication_details(db, raw_name)
    return {
        "is_valid": True,
        "status": "RECOGNIZED",
        "message": f"Medication '{details['drug_name']}' recognized in local knowledge base.",
        "raw_input": raw_name,
        "normalized_input": norm,
        "possible_actions": [],
        "details": details,
    }
