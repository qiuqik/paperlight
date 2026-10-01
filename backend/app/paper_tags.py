"""User-managed library labels, separate from generated publication evidence."""
from datetime import date
from pathlib import Path
import json
from pydantic import BaseModel, ConfigDict, Field, field_validator

class PaperTags(BaseModel):
    model_config = ConfigDict(extra="forbid")
    venue: str = Field(default="", max_length=120)
    publishDate: str = ""
    institutions: list[str] = Field(default_factory=list, max_length=30)
    other: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("venue")
    @classmethod
    def clean_venue(cls, value: str) -> str:
        return value.strip()

    @field_validator("publishDate")
    @classmethod
    def valid_date(cls, value: str) -> str:
        if value and date.fromisoformat(value).isoformat() != value:
            raise ValueError("Use YYYY-MM-DD")
        return value

    @field_validator("institutions", "other")
    @classmethod
    def clean_labels(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if any(len(value) > 160 for value in cleaned):
            raise ValueError("Label too long")
        return cleaned

def read_tags(folder: Path) -> dict | None:
    path = folder / "paper-tags.json"
    if not path.is_file():
        return None
    try:
        return PaperTags.model_validate_json(path.read_text(encoding="utf-8")).model_dump()
    except (ValueError, OSError):
        return None
