from datetime import date as Date
from decimal import Decimal
from typing import Annotated, Literal
from pydantic import BaseModel, Field, AwareDatetime, model_validator

Role = Literal["admin", "staff", "cook", "user", "technical"]
Quantity = Annotated[
    Decimal, Field(ge=0, max_digits=15, decimal_places=3, allow_inf_nan=False)
]
Temperature = Annotated[
    Decimal, Field(ge=-100, le=150, decimal_places=3, allow_inf_nan=False)
]
Name = Annotated[str, Field(min_length=1, max_length=150, pattern=r".*\S.*")]


class Strict(BaseModel):
    model_config = {"extra": "forbid"}


class Credentials(Strict):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=500)


class UserCreate(Credentials):
    role: Role = "user"


class UserUpdate(Strict):
    role: Role | None = None
    active: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=500)


class CategoryInput(Strict):
    name: Name
    items: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=500
    )


class TemplateInput(Strict):
    name: Name
    categories: list[CategoryInput] = Field(max_length=100)
    active: bool = True


class OperationInput(Strict):
    name: Name
    type: Literal["Einsatz", "Übung", "Veranstaltung"] = "Einsatz"
    date: Date
    end_date: Date | None = None
    people: int = Field(default=0, ge=0, le=100000)
    owner: str = Field(default="", max_length=120)
    location: str = Field(default="", max_length=180)
    template_ids: list[int] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def dates(self):
        if self.end_date and self.end_date < self.date:
            raise ValueError("Enddatum liegt vor dem Beginn")
        return self


class OperationPatch(Strict):
    name: Name | None = None
    type: Literal["Einsatz", "Übung", "Veranstaltung"] | None = None
    date: Date | None = None
    end_date: Date | None = None
    people: int | None = Field(default=None, ge=0, le=100000)
    owner: str | None = Field(default=None, max_length=120)
    location: str | None = Field(default=None, max_length=180)
    finished: bool | None = None


class ItemInput(Strict):
    label: str = Field(min_length=1, max_length=200)
    category: Name


class ItemPatch(Strict):
    done: bool | None = None
    quantity: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=500)


class ArticleInput(Strict):
    name: Name
    kind: Literal["Lebensmittel", "Getränk"] = "Lebensmittel"
    unit: Literal["Stück", "Liter", "kg"]
    target: Quantity = Decimal(0)
    active: bool = True


class MovementInput(Strict):
    article_id: int
    direction: Literal["in", "out", "inventory"]
    quantity: Quantity
    reason: Literal[
        "Zugang", "Verbrauch", "Verderb", "Rückgabe", "Inventurkorrektur", "Korrektur"
    ]
    note: str = Field(min_length=1, max_length=500)
    event_id: int | None = None
    external_id: str | None = Field(default=None, min_length=1, max_length=100)


class DeviceInput(Strict):
    name: Name
    location: str = Field(min_length=1, max_length=180)
    kind: Literal["Kühllager", "Tiefkühllager"] = "Kühllager"
    lower: Temperature
    upper: Temperature
    active: bool = True

    @model_validator(mode="after")
    def limits(self):
        if self.lower > self.upper:
            raise ValueError("Untergrenze muss kleiner als Obergrenze sein")
        return self


class ReadingInput(Strict):
    device_id: int
    value: Temperature
    measured_at: AwareDatetime
    event_id: int | None = None
    external_id: str | None = Field(default=None, min_length=1, max_length=100)


class NoteInput(Strict):
    text: str = Field(min_length=1, max_length=500)


class KeyInput(Strict):
    user_id: int
    name: Name
    scopes: list[Literal["stock:read", "stock:write", "temperature:write"]]
    device_ids: list[int] = Field(default_factory=list, max_length=100)


class FormInput(Strict):
    template_id: int
    event_id: int | None = None
    title: Name
    correction_of: int | None = None


class FormPatch(Strict):
    values: dict[str, str | bool]
    extra_pages: int = Field(default=0, ge=0, le=100)
    revision: int


class FinalizeInput(Strict):
    revision: int


class ExportInput(Strict):
    start: AwareDatetime
    end: AwareDatetime
    interval_hours: int = Field(default=1, ge=1, le=168)
    include_notes: bool = True
    event_id: int | None = None


class SettingsInput(Strict):
    ov_name: Name
