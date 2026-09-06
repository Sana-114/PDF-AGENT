import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session


@dataclass(slots=True)
class SkillContext:
    db: Session


@dataclass(slots=True)
class SkillDefinition:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[BaseModel, SkillContext], Any]
    requires_llm: bool = False

    def public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
            "requires_llm": self.requires_llm,
        }


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, SkillDefinition] = {}

    def register(self, definition: SkillDefinition) -> None:
        if definition.name in self._skills:
            raise ValueError(f"Skill 已注册: {definition.name}")
        self._skills[definition.name] = definition

    def list(self) -> list[SkillDefinition]:
        return sorted(self._skills.values(), key=lambda item: item.name)

    async def execute(self, name: str, arguments: dict[str, Any], context: SkillContext) -> Any:
        try:
            definition = self._skills[name]
        except KeyError as exc:
            raise KeyError(f"未知 Skill: {name}") from exc
        validated = definition.input_model.model_validate(arguments)
        result = definition.handler(validated, context)
        if inspect.isawaitable(result):
            return await result
        return result


skill_registry = SkillRegistry()

