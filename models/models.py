from datetime import date, datetime
from typing import Any, List, Optional

from sqlalchemy import Date, DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.database import Base


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_identifier: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    date_of_birth: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    sex: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    allergy_information: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    medications: Mapped[List["Medication"]] = relationship(
        back_populates="patient", cascade="all, delete-orphan"
    )
    labs: Mapped[List["Lab"]] = relationship(
        back_populates="patient", cascade="all, delete-orphan"
    )
    orders: Mapped[List["Order"]] = relationship(
        back_populates="patient", cascade="all, delete-orphan"
    )
    events: Mapped[List["Event"]] = relationship(
        back_populates="patient"
    )
    findings: Mapped[List["Finding"]] = relationship(
        back_populates="patient", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Patient(id={self.id}, identifier='{self.patient_identifier}', name='{self.name}')>"


class Medication(Base):
    __tablename__ = "medications"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    drug_name: Mapped[str] = mapped_column(String(255), index=True)
    rxnorm_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    dose: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    dose_unit: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    route: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    frequency: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, default="active")
    start_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    patient: Mapped["Patient"] = relationship(back_populates="medications")
    orders: Mapped[List["Order"]] = relationship(back_populates="medication")

    def __repr__(self) -> str:
        return f"<Medication(id={self.id}, drug_name='{self.drug_name}', status='{self.status}')>"


class Lab(Base):
    __tablename__ = "labs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    test_name: Mapped[str] = mapped_column(String(255), index=True)
    value: Mapped[str] = mapped_column(String(100))
    unit: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    reference_range: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    measured_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    patient: Mapped["Patient"] = relationship(back_populates="labs")

    def __repr__(self) -> str:
        unit_str = f" {self.unit}" if self.unit else ""
        return f"<Lab(id={self.id}, test_name='{self.test_name}', value='{self.value}{unit_str}')>"


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    medication_id: Mapped[Optional[int]] = mapped_column(ForeignKey("medications.id"), nullable=True, index=True)
    drug_name: Mapped[str] = mapped_column(String(255))
    dose: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    dose_unit: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    route: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    frequency: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, default="pending")
    ordered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    patient: Mapped["Patient"] = relationship(back_populates="orders")
    medication: Mapped[Optional["Medication"]] = relationship(back_populates="orders")

    def __repr__(self) -> str:
        return f"<Order(id={self.id}, drug_name='{self.drug_name}', status='{self.status}')>"


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[Optional[int]] = mapped_column(ForeignKey("patients.id"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    payload: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    patient: Mapped[Optional["Patient"]] = relationship(back_populates="events")

    def __repr__(self) -> str:
        return f"<Event(id={self.id}, event_type='{self.event_type}')>"


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[Optional[int]] = mapped_column(ForeignKey("patients.id"), nullable=True, index=True)
    rule_id: Mapped[str] = mapped_column(String(100), index=True)
    severity: Mapped[str] = mapped_column(String(50), index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    action: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    inputs: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    trace: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    patient: Mapped[Optional["Patient"]] = relationship(back_populates="findings")

    def __repr__(self) -> str:
        return f"<Finding(id={self.id}, rule_id='{self.rule_id}', severity='{self.severity}')>"
