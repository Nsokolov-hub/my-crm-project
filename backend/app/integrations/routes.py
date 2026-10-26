"""Integration endpoints by subdomain; app.main includes this router."""

from fastapi import APIRouter

from . import site_conversion, site_events, site_manufacturers, site_reports, site_requests, site_subscribers

router = APIRouter()
# Fixed paths (/site-requests/report, /site-requests/export.xlsx) before /site-requests/{id}.
router.include_router(site_reports.router)
router.include_router(site_requests.router)
router.include_router(site_conversion.router)
router.include_router(site_manufacturers.router)
router.include_router(site_subscribers.router)
_ = site_events
