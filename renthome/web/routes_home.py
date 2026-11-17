"""Panel de inicio."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from . import criteria as criteria_lib
from . import matching
from .deps import get_db, render, require_user
from .models import User
from .routes_profile import profile_completion

router = APIRouter()
DASHBOARD_LISTINGS = 5


@router.get("/")
def dashboard(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    steps = profile_completion(user)
    pref = user.preference
    has_areas = bool(pref and criteria_lib.area_keys(pref))
    matches = matching.find(db, pref, user.id) if has_areas else []
    return render(request, "dashboard.html", user=user, steps=steps, pref=pref, has_areas=has_areas,
                  matches=matches[:DASHBOARD_LISTINGS], total=len(matches),
                  new_count=sum(1 for m in matches if m.is_new), back="/",
                  summary=criteria_lib.summary(pref) if has_areas else "",
                  done=sum(1 for s in steps if s["done"]))


@router.get("/api/health")
def health() -> dict:
    return {"ok": True}
