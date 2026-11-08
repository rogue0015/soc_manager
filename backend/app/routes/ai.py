"""HTTP routes for the AI generation tab: personas, generating drafts, and the history.

Kept thin like routes/api.py. Nothing here posts anything: drafts are opened in the
post window, where you schedule or post them yourself. See ADR 0012.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from ..schemas import DraftTrashIn, DraftUsedIn, GenerateIn, PersonaIn, RegenerateIn

router = APIRouter(prefix="/api/ai")


@router.get("/status")
def ai_status(request: Request):
    """Whether a Gemini key is set, and which model is used. No call to Gemini."""
    return request.app.state.ai.status()


# ---- personas ----

@router.get("/personas")
def list_personas(request: Request):
    return request.app.state.personas.list()


@router.post("/personas", status_code=201)
def create_persona(data: PersonaIn, request: Request):
    return request.app.state.personas.create(data.model_dump())


@router.post("/personas/starters", status_code=201)
def add_starter_personas(request: Request):
    """Add the starter personas that aren't there yet."""
    return request.app.state.personas.add_starters()


@router.put("/personas/{persona_id}")
def update_persona(persona_id: int, data: PersonaIn, request: Request):
    return request.app.state.personas.update(persona_id, data.model_dump())


@router.delete("/personas/{persona_id}", status_code=204)
def delete_persona(persona_id: int, request: Request):
    request.app.state.personas.delete(persona_id)


# ---- generating and the history ----

@router.post("/generate", status_code=201)
def generate(data: GenerateIn, request: Request):
    """One draft per platform, from Gemini. Sends the idea or blog text to Google; posts nothing."""
    return request.app.state.ai.generate(data.source, data.idea, data.blog_url, data.event.model_dump(),
                                         data.platforms, data.persona_id)


@router.get("/generations")
def list_generations(request: Request, limit: int = Query(30, ge=1, le=200)):
    return request.app.state.ai.history(limit)


@router.get("/generations/{gen_id}")
def get_generation(gen_id: int, request: Request):
    return request.app.state.ai.get(gen_id)


@router.delete("/generations/{gen_id}", status_code=204)
def delete_generation(gen_id: int, request: Request):
    request.app.state.ai.delete(gen_id)


@router.post("/generations/{gen_id}/regenerate")
def regenerate(gen_id: int, data: RegenerateIn, request: Request):
    """A new draft for one platform, from the same inputs and persona."""
    return request.app.state.ai.regenerate(gen_id, data.platform)


@router.put("/generations/{gen_id}/drafts/{platform}/trash")
def trash_draft(gen_id: int, platform: str, data: DraftTrashIn, request: Request):
    """Trash a draft (or restore it with {"trashed": false}). The history keeps it."""
    return request.app.state.ai.set_trashed(gen_id, platform, data.trashed)


@router.put("/generations/{gen_id}/drafts/{platform}/post")
def draft_used(gen_id: int, platform: str, data: DraftUsedIn, request: Request):
    """Remember that a draft became a post (saved in the post window)."""
    return request.app.state.ai.mark_used(gen_id, platform, data.post_id)
