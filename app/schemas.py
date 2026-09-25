from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class PatientBase(BaseModel):
    patient_identifier: str = Field(..., min_length=1, max_length=100, description="Unique patient identifier / MRN")
    name: str = Field(..., min_length=1, max_length=255, description="Full patient name")
    date_of_birth: Optional[date] = Field(None, description="Date of birth")
    sex: Optional[str] = Field(None, max_length=50, description="Sex / Gender")
    allergy_information: Optional[str] = Field(None, description="Documented patient allergies")


class PatientCreate(PatientBase):
    pass


class PatientResponse(PatientBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


class MedicationBase(BaseModel):
    drug_name: str = Field(..., min_length=1, max_length=255, description="Name of the prescribed drug")
    rxnorm_code: Optional[str] = Field(None, max_length=50, description="RxNorm identifier")
    dose: Optional[str] = Field(None, max_length=100, description="Dose amount")
    dose_unit: Optional[str] = Field(None, max_length=50, description="Dose measurement unit")
    route: Optional[str] = Field(None, max_length=50, description="Route of administration")
    frequency: Optional[str] = Field(None, max_length=100, description="Dosing frequency")
    status: Optional[str] = Field("active", max_length=50, description="Medication status")
    start_date: Optional[datetime] = Field(None, description="Medication start datetime")
    end_date: Optional[datetime] = Field(None, description="Medication end datetime")


class MedicationCreate(MedicationBase):
    pass


class MedicationResponse(MedicationBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: int
    created_at: datetime


class LabBase(BaseModel):
    test_name: str = Field(..., min_length=1, max_length=255, description="Name of laboratory test")
    value: str = Field(..., min_length=1, max_length=100, description="Measured lab value / result")
    unit: Optional[str] = Field(None, max_length=50, description="Unit of measurement")
    reference_range: Optional[str] = Field(None, max_length=100, description="Laboratory reference range")
    measured_at: Optional[datetime] = Field(None, description="Timestamp of test measurement")


class LabCreate(LabBase):
    pass


class LabResponse(LabBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: int
    created_at: datetime


class OrderBase(BaseModel):
    drug_name: str = Field(..., min_length=1, max_length=255, description="Name of ordered medication")
    medication_id: Optional[int] = Field(None, description="Optional foreign key to existing medication record")
    dose: Optional[str] = Field(None, max_length=100, description="Dose amount")
    dose_unit: Optional[str] = Field(None, max_length=50, description="Dose unit")
    route: Optional[str] = Field(None, max_length=50, description="Route of administration")
    frequency: Optional[str] = Field(None, max_length=100, description="Dosing frequency")
    status: Optional[str] = Field("pending", max_length=50, description="Order status")
    ordered_at: Optional[datetime] = Field(None, description="Timestamp of the order")


class OrderCreate(OrderBase):
    pass


class OrderResponse(OrderBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: int
    ordered_at: datetime



