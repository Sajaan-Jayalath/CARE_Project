"""Propose, apply and undo text fixes for accepted concerns."""
from fastapi import APIRouter, Request
from pydantic import Field

from backend.ai.schemas.input_schema import StrictModel
from backend.dependencies import store
from backend.services import fix_service

router = APIRouter(prefix="/api/fixes", tags=["Fixes"])


class FixEdit(StrictModel):
    section_id: str = Field(min_length=1, max_length=200)
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    replacement: str = Field(max_length=4000)
    base: str = Field(max_length=20000)  # The wording the proposal was made from.


class FixInput(StrictModel):
    edits: list[FixEdit] = Field(min_length=1, max_length=50)
    comment: str = Field(default="", max_length=4000)


@router.post("/{job_id}/{finding_id}/proposal")
def propose_fix(job_id: str, finding_id: str, request: Request):
    return fix_service.propose(store(request), job_id, finding_id, request.app.state.rewriter_factory)


@router.put("/{job_id}/{finding_id}")
def apply_fix(job_id: str, finding_id: str, body: FixInput, request: Request):
    return fix_service.apply(store(request), job_id, finding_id, [e.model_dump() for e in body.edits], body.comment)


@router.delete("/{job_id}/{finding_id}")
def undo_fix(job_id: str, finding_id: str, request: Request):
    return fix_service.undo(store(request), job_id, finding_id)
