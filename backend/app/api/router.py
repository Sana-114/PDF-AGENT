from fastapi import APIRouter

from app.api.routes import agent, documents, health

api_router = APIRouter()
api_router.include_router(health.router, tags=["system"])
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])
api_router.include_router(agent.router, prefix="/agent", tags=["agent"])
