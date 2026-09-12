from typing import Literal

from pydantic import BaseModel, Field, field_validator

DiagramLayout = Literal["left-to-right", "top-down"]
DiagramFormat = Literal["mermaid", "graphviz", "tikz", "matplotlib"]


class DiagramGenerationRequest(BaseModel):
    title: str = Field(default="科研系统架构", min_length=1, max_length=160)
    idea: str = Field(min_length=3, max_length=8000)
    layout: DiagramLayout = "left-to-right"
    formats: list[DiagramFormat] = Field(
        default_factory=lambda: ["mermaid", "graphviz", "tikz", "matplotlib"],
        min_length=1,
        max_length=4,
    )

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("idea")
    @classmethod
    def strip_idea(cls, value: str) -> str:
        return value.strip()

    @field_validator("formats")
    @classmethod
    def deduplicate_formats(cls, value: list[DiagramFormat]) -> list[DiagramFormat]:
        return list(dict.fromkeys(value))


class DiagramNodeRead(BaseModel):
    node_id: str
    label: str
    x: float
    y: float


class DiagramEdgeRead(BaseModel):
    source: str
    target: str


class DiagramScriptRead(BaseModel):
    format: DiagramFormat
    language: str
    filename: str
    content: str


class DiagramGenerationRead(BaseModel):
    title: str
    layout: DiagramLayout
    nodes: list[DiagramNodeRead]
    edges: list[DiagramEdgeRead]
    canvas_width: int
    canvas_height: int
    scripts: list[DiagramScriptRead]
    warnings: list[str] = Field(default_factory=list)
