from fastapi import APIRouter
from pydantic import BaseModel

from app.core.assistant import forms

router = APIRouter(prefix="/forms", tags=["forms"])


class Suggest(BaseModel):
    label: str = ""  # the upload field's label, e.g. "Upload your resume (PDF)"
    page_title: str = ""  # fallback when the field has no label
    accept: str | None = None  # the input's accept attribute, e.g. ".pdf,image/*"


@router.post("/suggest")
def suggest(body: Suggest):
    """Ranked files for an upload field. Returns files only; nothing is attached or sent anywhere."""
    return {"results": forms.suggest(body.label, body.page_title, body.accept)}
