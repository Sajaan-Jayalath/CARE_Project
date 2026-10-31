"""Shared AI input contract. Text is preserved exactly for evidence offsets."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NonBlank = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Section(StrictModel):
    section_id: NonBlank
    text: NonBlank
    type: Literal["text", "ocr_text", "image_description"] = "text"
    page: int | None = Field(default=None, ge=1, strict=True)
    slide: int | None = Field(default=None, ge=1, strict=True)
    paragraph: int | None = Field(default=None, ge=1, strict=True)


class Document(StrictModel):
    document_id: NonBlank
    filename: NonBlank = "text-input"
    sections: list[Section] = Field(min_length=1, max_length=20000)

    @model_validator(mode="after")
    def unique_section_ids(self):
        ids = [s.section_id for s in self.sections]
        if len(ids) != len(set(ids)):
            raise ValueError("section_id values must be unique")
        return self

    @classmethod
    def from_text(cls, text: str, document_id: str = "text-input"):
        return cls(document_id=document_id, sections=[Section(section_id="s1", text=text)])
