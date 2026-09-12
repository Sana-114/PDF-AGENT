from fastapi import APIRouter

from app.api.routes import (
    agent,
    citation_graph,
    discovery,
    documents,
    health,
    recommendations,
    reviews,
    visualizations,
    writing,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["system"])
api_router.include_router(discovery.router, prefix="/discovery", tags=["discovery"])
api_router.include_router(
    recommendations.router, prefix="/recommendations", tags=["recommendations"]
)
api_router.include_router(citation_graph.router, prefix="/citation-graph", tags=["citation-graph"])
api_router.include_router(reviews.router, prefix="/reviews", tags=["reviews"])
api_router.include_router(
    visualizations.router, prefix="/visualizations", tags=["visualizations"]
)
api_router.include_router(writing.router, prefix="/writing", tags=["writing"])
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])
api_router.include_router(agent.router, prefix="/agent", tags=["agent"])
