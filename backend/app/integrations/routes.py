"""Integration endpoints by subdomain; app.main includes this router."""

from fastapi import APIRouter

from . import site_conversion, site_manufacturers, site_requests

router = APIRouter()
router.include_router(site_requests.router)
router.include_router(site_conversion.router)
router.include_router(site_manufacturers.router)
