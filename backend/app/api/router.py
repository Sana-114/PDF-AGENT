from fastapi import APIRouter

from app.api.routes import agent, discovery, documents, health, recommendations

api_router = APIRouter()
api_router.include_router(health.router, tags=["system"])
api_router.include_router(discovery.router, prefix="/discovery", tags=["discovery"])
api_router.include_router(
    recommendations.router, prefix="/recommendations", tags=["recommendations"]
)
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])
api_router.include_router(agent.router, prefix="/agent", tags=["agent"])
