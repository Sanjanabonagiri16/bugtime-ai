"""P5 draft PR publication endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db, http_error
from app.models.entities import PullRequestPublication
from app.schemas.publication import (
    GitHubStatusOut,
    PublicationOut,
    PublicationUpdate,
    PublishRequest,
)
from app.services import github_api
from app.services.publish import (
    PublishError,
    create_or_get_preview,
    publication_to_dict,
    read_patch_text,
    load_attempt,
    revalidate_on_current_branch,
    retry_publish,
    publish,
    update_preview,
)

router = APIRouter(tags=["publications"])


def _out(db: Session, pub: PullRequestPublication) -> PublicationOut:
    diff = None
    try:
        attempt = load_attempt(db, pub.patch_attempt_id)
        diff = read_patch_text(attempt)
    except PublishError:
        diff = None
    return PublicationOut(**publication_to_dict(pub, patch_diff=diff))


def _raise(exc: PublishError):
    status = 400
    if exc.code == "not_found":
        status = 404
    if exc.code in {"github_auth", "github_forbidden"}:
        status = 403
    if exc.code == "github_not_configured":
        status = 503
    raise http_error(status, exc.code, exc.message)


@router.get("/integrations/github/status", response_model=GitHubStatusOut)
def github_status():
    st = github_api.check_connection()
    return GitHubStatusOut(**st.to_dict())


@router.post("/patch-attempts/{attempt_id}/pr-preview", response_model=PublicationOut)
def prepare_preview(attempt_id: uuid.UUID, db: Session = Depends(get_db)):
    try:
        pub = create_or_get_preview(db, attempt_id)
        return _out(db, pub)
    except PublishError as exc:
        _raise(exc)


@router.get("/publications/{pub_id}", response_model=PublicationOut)
def get_publication(pub_id: uuid.UUID, db: Session = Depends(get_db)):
    pub = db.get(PullRequestPublication, pub_id)
    if not pub:
        raise http_error(404, "not_found", "Publication not found")
    return _out(db, pub)


@router.get("/investigations/{inv_id}/publications", response_model=list[PublicationOut])
def list_publications(inv_id: uuid.UUID, db: Session = Depends(get_db)):
    rows = (
        db.query(PullRequestPublication)
        .filter(PullRequestPublication.investigation_id == inv_id)
        .order_by(PullRequestPublication.created_at.desc())
        .all()
    )
    return [_out(db, p) for p in rows]


@router.patch("/publications/{pub_id}", response_model=PublicationOut)
def patch_publication(pub_id: uuid.UUID, body: PublicationUpdate, db: Session = Depends(get_db)):
    try:
        pub = update_preview(db, pub_id, title=body.title, body=body.body)
        return _out(db, pub)
    except PublishError as exc:
        _raise(exc)


@router.post("/publications/{pub_id}/revalidate", response_model=PublicationOut)
def revalidate_publication(pub_id: uuid.UUID, db: Session = Depends(get_db)):
    try:
        pub = revalidate_on_current_branch(db, pub_id)
        return _out(db, pub)
    except PublishError as exc:
        _raise(exc)


@router.post("/publications/{pub_id}/publish", response_model=PublicationOut)
def publish_publication(pub_id: uuid.UUID, body: PublishRequest, db: Session = Depends(get_db)):
    try:
        pub = publish(db, pub_id, preview_hash_client=body.preview_hash)
        return _out(db, pub)
    except PublishError as exc:
        _raise(exc)


@router.post("/publications/{pub_id}/retry", response_model=PublicationOut)
def retry_publication(pub_id: uuid.UUID, db: Session = Depends(get_db)):
    try:
        pub = retry_publish(db, pub_id)
        return _out(db, pub)
    except PublishError as exc:
        _raise(exc)
