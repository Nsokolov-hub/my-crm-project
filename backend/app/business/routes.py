"""Business endpoints by subdomain; app.main includes this router."""

from fastapi import APIRouter

from . import (
    absences,
    calendar,
    reports,
    reviews,
    supplier_mail,
    supplier_orders,
    supplier_prices,
    supplier_stats,
)

router = APIRouter()
for domain in (calendar, reviews, absences, supplier_orders, supplier_mail, supplier_stats, supplier_prices,
               reports):
    router.include_router(domain.router)
