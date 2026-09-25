from datetime import date, datetime
from typing import Any, List, Optional

from sqlalchemy import Date, DateTime, ForeignKey, Index, JSON, String, Text
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


# --- Intelligence Layer Database Models ---


class Drug(Base):
    __tablename__ = "drugs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    drug_name: Mapped[str] = mapped_column(String(255), index=True)
    normalized_name: Mapped[str] = mapped_column(String(255), index=True)
    rxnorm_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(100), index=True)
    source_version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    label_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    label_evidences: Mapped[List["LabelEvidence"]] = relationship(
        back_populates="drug", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Drug(id={self.id}, name='{self.drug_name}', normalized='{self.normalized_name}')>"


class InteractionEvidence(Base):
    __tablename__ = "interaction_evidence"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    drug_a: Mapped[str] = mapped_column(String(255), index=True)
    drug_b: Mapped[str] = mapped_column(String(255), index=True)
    description: Mapped[str] = mapped_column(Text)
    severity: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(100), index=True)
    source_version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    evidence_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_interaction_pair", "drug_a", "drug_b"),
    )

    def __repr__(self) -> str:
        return f"<InteractionEvidence(id={self.id}, drug_a='{self.drug_a}', drug_b='{self.drug_b}', severity='{self.severity}', source='{self.source}')>"


class LabelEvidence(Base):
    __tablename__ = "label_evidence"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    drug_id: Mapped[Optional[int]] = mapped_column(ForeignKey("drugs.id"), nullable=True, index=True)
    drug_name: Mapped[str] = mapped_column(String(255), index=True)
    section: Mapped[str] = mapped_column(String(100), index=True)
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(100), index=True)
    source_version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    evidence_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    drug: Mapped[Optional["Drug"]] = relationship(back_populates="label_evidences")

    __table_args__ = (
        Index("ix_label_drug_section", "drug_name", "section"),
    )

    def __repr__(self) -> str:
        return f"<LabelEvidence(id={self.id}, drug_name='{self.drug_name}', section='{self.section}', source='{self.source}')>"


class RuleVersion(Base):
    __tablename__ = "rule_versions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    pack_name: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[str] = mapped_column(String(50), index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rules_count: Mapped[int] = mapped_column(default=0)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(50), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    safety_rules: Mapped[List["SafetyRule"]] = relationship(
        back_populates="rule_version", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<RuleVersion(id={self.id}, pack='{self.pack_name}', version='{self.version}')>"


class SafetyRule(Base):
    __tablename__ = "safety_rules"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rule_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    rule_type: Mapped[str] = mapped_column(String(50), index=True)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    drug_a: Mapped[str] = mapped_column(String(255), index=True)
    drug_b: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(100), index=True)
    source_version: Mapped[str] = mapped_column(String(50))
    evidence_id: Mapped[str] = mapped_column(String(100), index=True)
    evidence_text: Mapped[str] = mapped_column(Text)
    severity: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    action: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="active", index=True)
    version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("rule_versions.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    rule_version: Mapped[Optional["RuleVersion"]] = relationship(back_populates="safety_rules")

    __table_args__ = (
        Index("ix_safety_rule_pair", "drug_a", "drug_b"),
    )

    def __repr__(self) -> str:
        return f"<SafetyRule(id={self.id}, rule_id='{self.rule_id}', rule_type='{self.rule_type}')>"


class AuditLedger(Base):
    __tablename__ = "audit_ledger"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    timestamp: Mapped[str] = mapped_column(String(100), index=True)
    actor: Mapped[str] = mapped_column(String(100), index=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    payload: Mapped[Any] = mapped_column(JSON)
    prev_hash: Mapped[str] = mapped_column(String(64), index=True)
    hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    def __repr__(self) -> str:
        return f"<AuditLedger(id={self.id}, actor='{self.actor}', event_type='{self.event_type}')>"

