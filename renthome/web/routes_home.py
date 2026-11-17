"""Panel de inicio."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from . import listings
from .config import Settings
from .deps import get_settings, render, require_user
from .models import User
from .routes_profile import profile_completion

router = APIRouter()


@router.get("/")
def dashboard(request: Request, user: User = Depends(require_user), settings: Settings = Depends(get_settings)):
    steps = profile_completion(user)
    pref = user.preference
    wants_ibiza = bool(pref and "ibiza" in pref.areas.split(","))
    found = listings.matching(settings.listings_file, pref) if wants_ibiza else []
    return render(request, "dashboard.html", user=user, steps=steps, listings=found, pref=pref,
                  wants_ibiza=wants_ibiza, done=sum(1 for s in steps if s["done"]))


@router.get("/api/health")
def health() -> dict:
    return {"ok": True}
