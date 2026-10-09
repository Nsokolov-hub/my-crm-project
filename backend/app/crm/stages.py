"""Advance commercial milestones without reopening or regressing a request."""

from app.core.service import audit

COMMERCIAL_STAGES = (
    "new", "clarification", "collecting_quotes", "quote_given", "calculation",
    "proposal_sent", "composition_agreed", "awaiting_payment", "sale_confirmed",
)


def advance_stage(db, user, request, stage: str):
    current = {"quotes": "collecting_quotes", "accepted": "composition_agreed",
               "won": "sale_confirmed"}.get(request.commercial_stage, request.commercial_stage)
    if current not in COMMERCIAL_STAGES or COMMERCIAL_STAGES.index(current) >= COMMERCIAL_STAGES.index(stage):
        return
    before = {"commercial_stage": request.commercial_stage}
    request.commercial_stage = stage
    request.version += 1
    audit(db, user, "request", request.id, stage, before=before, after={"commercial_stage": stage})
