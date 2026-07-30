"""Data models.

Two layers, deliberately separate:

* ``Raw*`` / ``RichExtraction`` -- what the LLM returns. Every value is a
  verbatim string plus the source span it came from. No typing, no math,
  no enums. The model's only job is *locating* text.
* ``LoadSchema`` -- the exact 12-key contract UltraShip asked for.
  Produced from RichExtraction by deterministic Python (see project.py).

Keeping these apart is the central design decision of this pipeline. It
means every judgement call -- date locale, unit assumptions, multi-stop
flattening, charge mapping -- lives in code that can be unit-tested and
explained to a non-engineer, instead of inside a model call.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

EquipmentType = Literal["van", "reefer", "flatbed", "other"]
Confidence = Literal["high", "medium", "low"]
StopKind = Literal["pickup", "drop", "unknown"]


# --------------------------------------------------------------------------
# Layer 1: what the model returns
# --------------------------------------------------------------------------

class Located(BaseModel):
    """A value the model found in the document, with its evidence."""

    model_config = ConfigDict(extra="forbid")

    value_raw: Optional[str] = Field(
        default=None, description="Verbatim text of the value, exactly as printed."
    )
    span: Optional[str] = Field(
        default=None,
        description=(
            "A contiguous substring of the source document containing the value. "
            "Must be copied character-for-character. Used to verify the value "
            "was read and not invented."
        ),
    )


class RawCommodity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: Optional[str] = None
    weight_raw: Optional[str] = None
    quantity_raw: Optional[str] = None


class RawStop(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(description="1-based position as printed in the document.")
    kind: StopKind
    location_raw: Optional[str] = Field(
        default=None, description="Full address line(s) as printed."
    )
    date_raw: Optional[str] = Field(
        default=None, description="Shipping/Delivery date exactly as printed."
    )
    commodities: list[RawCommodity] = Field(default_factory=list)
    span: Optional[str] = None


class RawCharge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(description="Line-item label exactly as printed.")
    amount_raw: str = Field(description="Amount exactly as printed, e.g. '500.00 USD'.")
    span: Optional[str] = None


class RichExtraction(BaseModel):
    """The full, lossless read of the document."""

    model_config = ConfigDict(extra="forbid")

    reference_id: Located = Field(default_factory=Located)
    header_pickup_date: Located = Field(default_factory=Located)
    agreed_amount: Located = Field(default_factory=Located)
    equipment_raw: Located = Field(default_factory=Located)
    stops: list[RawStop] = Field(default_factory=list)
    charges: list[RawCharge] = Field(default_factory=list)
    total_raw: Located = Field(default_factory=Located)


# --------------------------------------------------------------------------
# Layer 2: the delivery contract
# --------------------------------------------------------------------------

class Place(BaseModel):
    model_config = ConfigDict(extra="forbid")

    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None


class LoadSchema(BaseModel):
    """Exactly the keys UltraShip specified. Nothing added, nothing renamed."""

    model_config = ConfigDict(extra="forbid")

    load_id: Optional[str] = None
    origin: Place = Field(default_factory=Place)
    destination: Place = Field(default_factory=Place)
    pickup_date: Optional[str] = None
    delivery_date: Optional[str] = None
    equipment_type: Optional[EquipmentType] = None
    line_haul_rate: Optional[float] = None
    fuel_surcharge: Optional[float] = None
    total_rate: Optional[float] = None
    weight_lbs: Optional[float] = None
    commodity: Optional[str] = None
    confidence: Confidence = "low"


# --------------------------------------------------------------------------
# Out-of-contract diagnostics
# --------------------------------------------------------------------------

Severity = Literal["fatal", "low", "medium", "info"]


class Warning_(BaseModel):
    """A named, machine-readable finding. Never free-form prose."""

    model_config = ConfigDict(extra="forbid")

    code: str
    severity: Severity
    detail: str


class Reconciliation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "balanced",
        "unmapped_charge",
        "arithmetic_mismatch",
        "missing_components",
        "missing_total",
        "no_rate_data",
    ]
    schema_sum: Optional[float] = Field(
        default=None, description="line_haul_rate + fuel_surcharge, i.e. what a "
        "consumer reading only the contract fields would compute."
    )
    document_total: Optional[float] = None
    delta: Optional[float] = None
    other_charges: list[dict] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    """What the pipeline returns. ``load`` is the contract; the rest is context."""

    model_config = ConfigDict(extra="forbid")

    load: LoadSchema
    meta: dict
