from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class GlossaryTerm(BaseModel):
    source: str = Field(min_length=1, max_length=120)
    target: str = Field(min_length=1, max_length=160)

    @field_validator("source", "target")
    @classmethod
    def clean_term(cls, value: str) -> str:
        return " ".join(value.split())


class AcademicTranslationRequest(BaseModel):
    text: str = Field(min_length=1, max_length=24000)
    source_language: Literal["auto", "zh", "en"] = "zh"
    target_language: Literal["zh", "en"] = "en"
    document_type: Literal["abstract", "paper"] = "abstract"
    glossary: list[GlossaryTerm] = Field(default_factory=list, max_length=50)

    @field_validator("text")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("待翻译文本不能为空。")
        return value.strip()

    @field_validator("glossary")
    @classmethod
    def deduplicate_glossary(cls, value: list[GlossaryTerm]) -> list[GlossaryTerm]:
        deduplicated: dict[str, GlossaryTerm] = {}
        for item in value:
            deduplicated[item.source] = item
        return list(deduplicated.values())

    @model_validator(mode="after")
    def reject_same_language(self) -> "AcademicTranslationRequest":
        if self.source_language != "auto" and self.source_language == self.target_language:
            raise ValueError("源语言与目标语言不能相同。")
        return self


class GlossaryApplicationRead(BaseModel):
    source: str
    target: str
    count: int = Field(ge=1)


class PreservationCheckRead(BaseModel):
    kind: Literal["formula", "code", "citation", "url", "number"]
    count: int = Field(ge=1)


class AcademicTranslationRead(BaseModel):
    translation: str
    source_language: Literal["auto", "zh", "en"]
    target_language: Literal["zh", "en"]
    document_type: Literal["abstract", "paper"]
    provider: str
    model: str | None = None
    source_characters: int
    translated_characters: int
    paragraph_count: int
    request_count: int
    glossary_applied: list[GlossaryApplicationRead] = Field(default_factory=list)
    preservation_checks: list[PreservationCheckRead] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
