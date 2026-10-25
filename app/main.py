import os
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
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
    ml_advisory,
    orders,
    patients,
    simulated_orders,
    substitutions,
    web_app,
)
from app.schemas import SystemStatusResponse
from database.database import Base, engine, get_db
from engine.auth.models import AuthBase
import models  # noqa: F401
from models import SubstitutionRequest

# Initialize all application and auth tables idempotently
Base.metadata.create_all(bind=engine)
AuthBase.metadata.create_all(bind=engine)
# Create the new workflow table without altering existing clinical tables.
SubstitutionRequest.__table__.create(bind=engine, checkfirst=True)

from sqlalchemy import inspect

with engine.begin() as conn:
    inspector = inspect(conn)
    tables = inspector.get_table_names()
    if "users" in tables:
        cols = {col["name"] for col in inspector.get_columns("users")}
        if cols:
            is_pg = conn.dialect.name == "postgresql"
            if "totp_secret" not in cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN totp_secret VARCHAR(64)"))
            if "totp_enabled" not in cols:
                default_val = "FALSE" if is_pg else "0"
                conn.execute(text(f"ALTER TABLE users ADD COLUMN totp_enabled BOOLEAN DEFAULT {default_val}"))
            if "totp_last_verified_at" not in cols:
                col_type = "TIMESTAMP" if is_pg else "DATETIME"
                conn.execute(text(f"ALTER TABLE users ADD COLUMN totp_last_verified_at {col_type}"))
            if "totp_last_timestep" not in cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN totp_last_timestep INTEGER"))


app = FastAPI(
    title="AEGIS Rx",
    description="Offline Medication Safety Platform",
    version="0.1.0",
)

# Configure CORS strictly for trusted local origins (no unrestricted wildcards with credentials)
cors_origins_env = os.getenv("AEGIS_CORS_ORIGINS", "")
trusted_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
if cors_origins_env:
    for origin in cors_origins_env.split(","):
        cleaned = origin.strip()
        if cleaned and cleaned not in trusted_origins:
            trusted_origins.append(cleaned)

app.add_middleware(
    CORSMiddleware,
    allow_origins=trusted_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(dashboards.router)
app.include_router(substitutions.router)
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
app.include_router(ml_advisory.router)


@app.get("/", summary="AEGIS Rx REST API Root & Directory")
def read_root():
    return {
        "name": "AEGIS Rx",
        "description": "Offline Medication Safety Platform - REST API",
        "version": "0.1.0",
        "status": "online",
        "mode": "offline-capable",
        "docs_url": "/docs",
        "openapi_url": "/openapi.json",
        "endpoints": {
            "auth": "/api/auth",
            "dashboards": "/api/dashboard",
            "substitutions": "/api/substitutions",
            "findings": "/findings",
            "medications": "/medications",
            "orders": "/orders",
            "patients": "/patients",
            "labs": "/labs",
            "events": "/events",
            "audit": "/audit",
            "ml_advisory": "/ml",
            "status": "/status",
            "health": "/health",
            "legacy_console": "/app",
        },
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

