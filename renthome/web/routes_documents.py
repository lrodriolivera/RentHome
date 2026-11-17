"""Subida, descarga y borrado de documentos del propio usuario."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from . import catalog, crypto, documents, forms
from .config import MAX_DOCUMENTS_PER_USER, MAX_UPLOAD_BYTES, Settings
from .deps import audit, flash, get_db, get_settings, render, require_user, verify_csrf
from .models import Document, User

router = APIRouter()


def own_document(db: Session, user: User, doc_id: int) -> Document:
    doc = db.get(Document, doc_id)
    # 404 también si es de otro usuario: no revelamos que existe
    if doc is None or doc.user_id != user.id:
        raise HTTPException(status_code=404)
    return doc


@router.get("/documentos")
def list_documents(request: Request, user: User = Depends(require_user)):
    docs = sorted(user.documents, key=lambda d: d.created_at, reverse=True)
    return render(request, "documents.html", user=user, documents=docs,
                  max_mb=MAX_UPLOAD_BYTES // (1024 * 1024))


@router.post("/documentos", dependencies=[Depends(require_user), Depends(verify_csrf)])
async def upload_document(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db),
                          settings: Settings = Depends(get_settings)):
    form = await request.form()
    upload = form.get("file")
    try:
        kind = forms.choice(form, "kind", catalog.DOCUMENT_KINDS)
        if not kind:
            raise documents.UploadError("Elige el tipo de documento.")
        if upload is None or not hasattr(upload, "read"):
            raise documents.UploadError("Selecciona un archivo.")
        if len(user.documents) >= MAX_DOCUMENTS_PER_USER:
            raise documents.UploadError(f"Has alcanzado el máximo de {MAX_DOCUMENTS_PER_USER} documentos.")
        content = await upload.read(MAX_UPLOAD_BYTES + 1)
        doc = documents.store(settings, user.id, kind, upload.filename or "documento", content)
    except documents.UploadError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/documentos", status_code=303)
    db.add(doc)
    audit(db, request, user.id, "documento_subido", kind)
    flash(request, "Documento guardado y cifrado.")
    return RedirectResponse("/documentos", status_code=303)


@router.get("/documentos/{doc_id}/descargar")
def download_document(doc_id: int, request: Request, user: User = Depends(require_user),
                      db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    doc = own_document(db, user, doc_id)
    try:
        content = documents.read(settings, doc)
    except (crypto.DecryptionError, FileNotFoundError) as exc:
        raise HTTPException(status_code=410, detail="El documento ya no está disponible.") from exc
    audit(db, request, user.id, "documento_descargado", doc.kind)
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(doc.filename)}",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
    }
    return Response(content, media_type=doc.mime, headers=headers)


@router.post("/documentos/{doc_id}/borrar", dependencies=[Depends(require_user), Depends(verify_csrf)])
def delete_document(doc_id: int, request: Request, user: User = Depends(require_user),
                    db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    doc = own_document(db, user, doc_id)
    documents.remove(settings, doc)
    db.delete(doc)
    audit(db, request, user.id, "documento_borrado", doc.kind)
    flash(request, "Documento eliminado.")
    return RedirectResponse("/documentos", status_code=303)
