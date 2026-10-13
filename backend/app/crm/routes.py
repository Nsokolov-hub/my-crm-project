"""CRM endpoints by subdomain; app.main includes this router."""

from fastapi import APIRouter

from . import activities, catalogue, counterparties, manufacturers, quotes, requests

router = APIRouter()
for domain in (catalogue, manufacturers, counterparties, activities, requests, quotes):
    router.include_router(domain.router)
