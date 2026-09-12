from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.academic_translation import GlossaryTerm

TranslationLanguage = Literal["zh", "en"]
TranslationSourceLanguage = Literal["auto", "zh", "en"]
TranslationDocumentType = Literal["abstract", "paper"]
TranslationDraftStatus = Literal["draft", "reviewed"]


class TranslationGlossaryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    source_language: TranslationLanguage
    target_language: TranslationLanguage
    terms: list[GlossaryTerm] = Field(min_length=1, max_length=50)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("terms")
    @classmethod
    def deduplicate_terms(cls, value: list[GlossaryTerm]) -> list[GlossaryTerm]:
        deduplicated = {item.source: item for item in value}
        return list(deduplicated.values())

    @model_validator(mode="after")
    def reject_same_language(self) -> "TranslationGlossaryCreate":
        if self.source_language == self.target_language:
            raise ValueError("术语库的源语言与目标语言不能相同。")
        return self


class TranslationGlossaryRead(BaseModel):
    id: str
    name: str
    source_language: TranslationLanguage
    target_language: TranslationLanguage
    terms: list[GlossaryTerm]
    term_count: int
    created_at: datetime
    updated_at: datetime


class TranslationGlossaryList(BaseModel):
    items: list[TranslationGlossaryRead]


class AcademicTranslationDraftCreate(BaseModel):
    title: str = Field(default="未命名译稿", min_length=1, max_length=180)
    source_text: str = Field(min_length=1, max_length=24000)
    translated_text: str = Field(min_length=1, max_length=48000)
    source_language: TranslationSourceLanguage
    target_language: TranslationLanguage
    document_type: TranslationDocumentType
    status: TranslationDraftStatus = "draft"
    glossary_id: str | None = Field(default=None, max_length=36)
    provider: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=160)

    @field_validator("title", "source_text", "translated_text")
    @classmethod
    def reject_blank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("译稿标题和正文不能为空。")
        return value.strip()

    @model_validator(mode="after")
    def reject_same_language(self) -> "AcademicTranslationDraftCreate":
        if self.source_language != "auto" and self.source_language == self.target_language:
            raise ValueError("源语言与目标语言不能相同。")
        return self


class AcademicTranslationDraftUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=180)
    translated_text: str | None = Field(default=None, min_length=1, max_length=48000)
    status: TranslationDraftStatus | None = None

    @field_validator("title", "translated_text")
    @classmethod
    def reject_blank_update(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("译稿标题和译文不能为空。")
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def require_change(self) -> "AcademicTranslationDraftUpdate":
        if self.title is None and self.translated_text is None and self.status is None:
            raise ValueError("至少提交一项译稿变更。")
        return self


class AcademicTranslationDraftRead(BaseModel):
    id: str
    title: str
    source_text: str
    translated_text: str
    source_language: TranslationSourceLanguage
    target_language: TranslationLanguage
    document_type: TranslationDocumentType
    status: TranslationDraftStatus
    glossary_id: str | None
    provider: str | None
    model: str | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AcademicTranslationDraftList(BaseModel):
    items: list[AcademicTranslationDraftRead]
    total: int
