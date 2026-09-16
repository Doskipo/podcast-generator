"""POST /api/interests/suggest — the settings UI's "suggest" button: one LLM
call drafting a one-line description plus event-shaped search queries for
an interest topic."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session

from podcast import db
from podcast.models import InterestSuggestion, LLMSettings, Profile
from podcast.stages.fetch import suggest_interest

router = APIRouter()


class InterestSuggestRequest(BaseModel):
    topic: str
    description: str | None = None


@router.post("/interests/suggest", response_model=InterestSuggestion)
def post_interest_suggest(
    body: InterestSuggestRequest, session: Session = Depends(db.get_session)
) -> InterestSuggestion:
    # Use the configured profile's cheap model if one exists yet, else the
    # same default LLMSettings.model would fall back to — no profile is
    # required to try this button before saving one for the first time.
    row = session.get(db.ProfileRecord, 1)
    model = Profile.model_validate(row.data).llm.model if row is not None else LLMSettings().model
    return suggest_interest(body.topic, body.description, model)
