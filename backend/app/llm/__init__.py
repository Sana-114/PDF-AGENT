def get_llm_provider(*args, **kwargs):
    """Load provider adapters on demand, not while importing shared claim types."""
    from app.llm.factory import get_llm_provider as factory

    return factory(*args, **kwargs)

__all__ = ["get_llm_provider"]

