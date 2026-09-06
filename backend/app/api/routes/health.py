from fastapi import APIRouter

from app.core.config import settings
from app.schemas.document import HealthRead

router = APIRouter()


@router.get("/health", response_model=HealthRead)
def health() -> HealthRead:
    return HealthRead(status="ok", service=settings.app_name, version=settings.app_version)
