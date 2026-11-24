"""Importable entities: columns, keys and references.

One definition drives the templates, parsing, validation, draft creation and the change summary.
Entities are listed in dependency order (parents first).

Each fact row is versioned (CommercialMixin). A row's *logical key* (e.g. project code + unit
number) is stable across versions; foreign keys point at a specific version row, so lookups
always go through the parent's code, never its id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from app.db.models import Amenity, Offer, PaymentPlan, PriceList, Project, Unit, UnitType

ColumnKind = Literal["text", "int", "money", "area", "date", "choice", "percent", "milestones"]

PROPERTY_TYPES = ("apartment", "villa", "plot", "row_house", "penthouse", "studio")
AVAILABILITY = ("available", "booked", "blocked", "sold")


@dataclass(frozen=True)
class Column:
    name: str
    kind: ColumnKind
    required: bool = False
    help: str = ""
    example: str = ""
    choices: tuple[str, ...] = ()
    max_length: int = 200
    min_value: int | None = None


@dataclass(frozen=True)
class Reference:
    """A column pair that must point at an existing row of another entity."""

    entity: str
    # local column names, in the order of the target entity's key
    columns: tuple[str, ...]
    error_code: str


@dataclass(frozen=True)
class Entity:
    name: str
    label: str
    model: Any  # one of the CommercialMixin models (typed loosely: they share columns)
    columns: tuple[Column, ...]
    key: tuple[str, ...]
    references: tuple[Reference, ...] = field(default_factory=tuple)

    @property
    def required(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns if c.required)

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)

    def column(self, name: str) -> Column:
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(name)


PROJECT_CODE = Column(
    "project_code",
    "text",
    required=True,
    max_length=64,
    help="Short unique code for the project, e.g. SG. Must match the projects sheet.",
    example="SG",
)
VALID_FROM = Column(
    "valid_from",
    "date",
    help="First day this row is valid (YYYY-MM-DD). Optional.",
    example="2026-10-01",
)
VALID_TO = Column(
    "valid_to",
    "date",
    help="Last day this row is valid (YYYY-MM-DD). After it the fact is stale. Optional.",
    example="2027-03-31",
)

PROJECTS = Entity(
    name="projects",
    label="Projects",
    model=Project,
    key=("project_code",),
    columns=(
        PROJECT_CODE,
        Column(
            "name",
            "text",
            required=True,
            help="Project name as buyers know it.",
            example="Sunrise Greens",
        ),
        Column("city", "text", required=True, max_length=100, example="Kolkata"),
        Column("locality", "text", example="New Town Action Area II"),
        Column("address", "text", max_length=500),
        Column("description", "text", max_length=2000),
        Column("rera_number", "text", max_length=100, help="RERA registration number."),
        Column(
            "possession_date",
            "date",
            help="Expected possession (YYYY-MM-DD).",
            example="2027-12-31",
        ),
        VALID_FROM,
        VALID_TO,
    ),
)

UNIT_TYPES = Entity(
    name="unit_types",
    label="Unit types",
    model=UnitType,
    key=("project_code", "unit_type_code"),
    references=(Reference("projects", ("project_code",), "unknown_project"),),
    columns=(
        PROJECT_CODE,
        Column(
            "unit_type_code",
            "text",
            required=True,
            max_length=64,
            help="Code for the configuration within the project, e.g. 2BHK-A.",
            example="2BHK-A",
        ),
        Column("name", "text", required=True, example="2 BHK Classic"),
        Column(
            "property_type", "choice", required=True, choices=PROPERTY_TYPES, example="apartment"
        ),
        Column(
            "bhk",
            "int",
            required=True,
            min_value=0,
            help="Bedrooms (0 for studio/plot).",
            example="2",
        ),
        Column("carpet_area_sqft", "area", help="Carpet area in square feet.", example="850"),
        Column(
            "super_area_sqft", "area", help="Super built-up area in square feet.", example="1120"
        ),
        VALID_FROM,
        VALID_TO,
    ),
)

UNITS = Entity(
    name="units",
    label="Units",
    model=Unit,
    key=("project_code", "unit_number"),
    references=(
        Reference("projects", ("project_code",), "unknown_project"),
        Reference("unit_types", ("project_code", "unit_type_code"), "unknown_unit_type"),
    ),
    columns=(
        PROJECT_CODE,
        Column("unit_number", "text", required=True, max_length=64, example="A-1203"),
        Column(
            "unit_type_code",
            "text",
            max_length=64,
            help="Unit type code from the unit_types sheet. Optional.",
            example="2BHK-A",
        ),
        Column("floor", "int", example="12"),
        Column("bhk", "int", required=True, min_value=0, example="2"),
        Column(
            "property_type", "choice", required=True, choices=PROPERTY_TYPES, example="apartment"
        ),
        Column("carpet_area_sqft", "area", example="850"),
        Column(
            "price_inr",
            "money",
            help="All-inclusive price in whole rupees, no lakh/crore words. 72,00,000 or 7200000.",
            example="7200000",
        ),
        Column("availability", "choice", required=True, choices=AVAILABILITY, example="available"),
        Column("facing", "text", max_length=64, example="East"),
        VALID_FROM,
        VALID_TO,
    ),
)

PRICE_LISTS = Entity(
    name="price_lists",
    label="Price lists",
    model=PriceList,
    key=("project_code", "unit_type_code", "unit_number"),
    references=(
        Reference("projects", ("project_code",), "unknown_project"),
        Reference("unit_types", ("project_code", "unit_type_code"), "unknown_unit_type"),
        Reference("units", ("project_code", "unit_number"), "unknown_unit"),
    ),
    columns=(
        PROJECT_CODE,
        Column(
            "unit_type_code",
            "text",
            max_length=64,
            help="Price for a unit type. Leave empty when pricing a single unit.",
        ),
        Column(
            "unit_number",
            "text",
            max_length=64,
            help="Price for one unit. Leave empty when pricing a unit type.",
        ),
        Column(
            "base_price_inr",
            "money",
            required=True,
            help="Starting price in rupees.",
            example="7200000",
        ),
        Column(
            "price_max_inr",
            "money",
            help="Top of the price range in rupees. Optional.",
            example="7800000",
        ),
        Column("price_per_sqft_inr", "money", example="8500"),
        Column("notes", "text", max_length=1000),
        VALID_FROM,
        VALID_TO,
    ),
)

OFFERS = Entity(
    name="offers",
    label="Offers",
    model=Offer,
    key=("project_code", "title"),
    references=(Reference("projects", ("project_code",), "unknown_project"),),
    columns=(
        PROJECT_CODE,
        Column("title", "text", required=True, example="Puja Offer"),
        Column("description", "text", max_length=2000),
        Column(
            "discount_inr",
            "money",
            help="Flat discount in rupees. Use this or discount_percent, not both.",
            example="200000",
        ),
        Column("discount_percent", "percent", help="Percentage discount, e.g. 5 or 2.5."),
        VALID_FROM,
        VALID_TO,
    ),
)

PAYMENT_PLANS = Entity(
    name="payment_plans",
    label="Payment plans",
    model=PaymentPlan,
    key=("project_code", "name"),
    references=(Reference("projects", ("project_code",), "unknown_project"),),
    columns=(
        PROJECT_CODE,
        Column("name", "text", required=True, example="Construction Linked Plan"),
        Column("description", "text", max_length=2000),
        Column(
            "milestones",
            "milestones",
            required=True,
            max_length=1000,
            help="Stage:percent pairs separated by semicolons, adding up to 100.",
            example="Booking:10; Agreement:20; Structure:40; Possession:30",
        ),
        VALID_FROM,
        VALID_TO,
    ),
)

AMENITIES = Entity(
    name="amenities",
    label="Amenities",
    model=Amenity,
    key=("project_code", "name"),
    references=(Reference("projects", ("project_code",), "unknown_project"),),
    columns=(
        PROJECT_CODE,
        Column("name", "text", required=True, example="Swimming pool"),
        Column("category", "text", max_length=64, example="Leisure"),
        VALID_FROM,
        VALID_TO,
    ),
)

ENTITIES: tuple[Entity, ...] = (
    PROJECTS,
    UNIT_TYPES,
    UNITS,
    PRICE_LISTS,
    OFFERS,
    PAYMENT_PLANS,
    AMENITIES,
)
ENTITY_BY_NAME: dict[str, Entity] = {e.name: e for e in ENTITIES}
ENTITY_ORDER: dict[str, int] = {e.name: i for i, e in enumerate(ENTITIES)}

# Entity type stored on import_batches for multi-sheet workbooks and documents.
WORKBOOK = "inventory"
DOCUMENT = "document"
