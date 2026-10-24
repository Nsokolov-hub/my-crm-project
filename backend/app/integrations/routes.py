"""Integration endpoints by subdomain; app.main includes this router."""

from fastapi import APIRouter

from . import site_requests

router = APIRouter()
router.include_router(site_requests.router)
