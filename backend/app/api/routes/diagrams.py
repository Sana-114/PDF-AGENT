from fastapi import APIRouter, HTTPException

from app.schemas.diagram import DiagramGenerationRead, DiagramGenerationRequest
from app.services.architecture_diagram import (
    ArchitectureDiagramError,
    generate_architecture_diagram,
)

router = APIRouter()


@router.post("/generate", response_model=DiagramGenerationRead)
def generate_diagram(request: DiagramGenerationRequest) -> DiagramGenerationRead:
    try:
        return generate_architecture_diagram(
            request.title,
            request.idea,
            request.layout,
            request.formats,
        )
    except ArchitectureDiagramError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
