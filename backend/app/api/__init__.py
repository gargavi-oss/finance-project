"""API package."""

from app.api.auth import router as auth_router  # noqa: F401
from app.api.documents import router as documents_router  # noqa: F401
from app.api.system import router as system_router  # noqa: F401