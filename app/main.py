from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.routers import labs, medications, orders, patients
from database.database import get_db

app = FastAPI(
    title="AEGIS Rx",
    description="Offline Medication Safety Platform",
    version="0.1.0",
)

app.include_router(patients.router)
app.include_router(medications.router)
app.include_router(labs.router)
app.include_router(orders.router)






@app.get("/")
def read_root():
    return {
        "name": "AEGIS Rx",
        "description": "Offline Medication Safety Platform",
        "version": "0.1.0",
        "status": "online",
    }


@app.get("/health")
def health_check():
    return {"status": "ok"}


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

