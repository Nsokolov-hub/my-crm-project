"""Business endpoints by subdomain; app.main includes this router."""

from fastapi import APIRouter

from . import absences, calendar, reports, reviews, supplier_mail, supplier_orders

router = APIRouter()
for domain in (calendar, reviews, absences, supplier_orders, supplier_mail, reports):
    router.include_router(domain.router)
