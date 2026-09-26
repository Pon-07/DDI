from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.dependencies import get_audit_ledger, get_resolution_engine
from app.routers import (
    audit,
    auth,
    dashboards,
    demo,
    events,
    findings,
    labs,
    medications,
    orders,
    patients,
    simulated_orders,
    web_app,
)
from app.schemas import SystemStatusResponse
from database.database import engine, get_db
from engine.auth.models import AuthBase

# Initialize auth tables idempotently
AuthBase.metadata.create_all(bind=engine)

with engine.begin() as conn:
    cols = [
        row[1]
        for row in conn.execute(text("PRAGMA table_info(users)")).fetchall()
    ]
    if cols:
        if "totp_secret" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN totp_secret VARCHAR(64)"))
        if "totp_enabled" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN totp_enabled BOOLEAN DEFAULT 0"))
        if "totp_last_verified_at" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN totp_last_verified_at DATETIME"))
        if "totp_last_timestep" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN totp_last_timestep INTEGER"))


app = FastAPI(
    title="AEGIS Rx",
    description="Offline Medication Safety Platform",
    version="0.1.0",
)

app.include_router(auth.router)
app.include_router(dashboards.router)
app.include_router(web_app.router)
app.include_router(patients.router)
app.include_router(medications.router)
app.include_router(labs.router)
app.include_router(orders.router)
app.include_router(events.router)
app.include_router(findings.router)
app.include_router(simulated_orders.router)
app.include_router(audit.router)
app.include_router(demo.router)


@app.get("/")
def read_root():
    return {
        "name": "AEGIS Rx",
        "description": "Offline Medication Safety Platform",
        "version": "0.1.0",
        "status": "online",
        "mode": "offline-capable",
    }


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/status", response_model=SystemStatusResponse)
@app.get("/health/status", response_model=SystemStatusResponse)
def get_system_status(
    db: Session = Depends(get_db),
    resolution_engine=Depends(get_resolution_engine),
    audit_ledger=Depends(get_audit_ledger),
):
    # Check DB
    try:
        db_res = db.execute(text("SELECT 1")).scalar()
        db_status = "connected" if db_res == 1 else "error"
    except Exception:
        db_status = "error"

    # Active rules
    active_rules = resolution_engine.get_active_rules() if hasattr(resolution_engine, "get_active_rules") else []

    # Audit chain
    verification = audit_ledger.verify_chain()

    return SystemStatusResponse(
        status="ok",
        offline_capable=True,
        database=db_status,
        rule_pack="aegis_hackathon_demo",
        active_rules_count=len(active_rules),
        ollama_available=False,
        audit_chain_valid=verification.is_valid,
        total_audit_records=verification.total_records,
    )


@app.get("/health/db")
def health_check_db(db: Session = Depends(get_db)):
    try:
        result = db.execute(text("SELECT 1")).scalar()
        if result != 1:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database query returned unexpected result",
            )
        return {
            "status": "ok",
            "database": "connected",
        }
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database connection error: {str(exc)}",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Unexpected database error: {str(exc)}",
        ) from exc

