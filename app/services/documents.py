"""Stage 8: document library with versions, validation and storage limits."""
import hashlib
import io
import os
import zipfile

from flask import current_app
from sqlalchemy import func, select
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import DOCUMENT_CATEGORIES, Document, DocumentComment, DocumentVersion, Project, User

from . import audit
from . import projects as project_svc
from .limits import LimitExceeded, SubscriptionInactive, ensure_within_limit
from .notify import notify
from .storage import get_storage, new_key


class DocumentError(ValueError):
    pass


OFFICE_PREFIX = {"docx": "word/", "xlsx": "xl/", "pptx": "ppt/"}
ALLOWED = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "doc": "application/msword",
    "xls": "application/vnd.ms-excel",
    "ppt": "application/vnd.ms-powerpoint",
    "csv": "text/csv",
    "txt": "text/plain",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
}


# ---------------------------------------------------------------- access
def can_view(user: User, doc: Document) -> bool:
    if not user.can("documents.view"):
        return False
    return doc.project_id is None or user.can("projects.view")


def can_upload(user: User, project: Project | None) -> bool:
    if user.can("documents.manage"):
        return True
    return project is not None and (project_svc.can_edit(user, project) or
                                    any(m.user_id == user.id for m in project.members))


def can_manage(user: User, doc: Document) -> bool:
    return (user.can("documents.manage") or doc.created_by_id == user.id or
            (doc.project is not None and project_svc.can_edit(user, doc.project)))


# ---------------------------------------------------------------- validation
def _looks_like(ext: str, data: bytes) -> bool:
    if ext == "pdf":
        return data[:5] == b"%PDF-"
    if ext == "png":
        return data[:8] == b"\x89PNG\r\n\x1a\n"
    if ext in ("jpg", "jpeg"):
        return data[:3] == b"\xff\xd8\xff"
    if ext in ("doc", "xls", "ppt"):
        return data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    if ext in OFFICE_PREFIX:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
                if any(n.lower().endswith(("vbaproject.bin",)) for n in names):
                    return False  # macro-enabled content disguised as a normal file
                return "[Content_Types].xml" in names and any(n.startswith(OFFICE_PREFIX[ext]) for n in names)
        except zipfile.BadZipFile:
            return False
    if ext in ("csv", "txt"):
        if b"\x00" in data[:8192]:
            return False
        try:
            data[:65536].decode("utf-8")
        except UnicodeDecodeError:
            try:
                data[:65536].decode("latin-1")
            except UnicodeDecodeError:
                return False
        return True
    return False


def validate_upload(file_storage) -> tuple[str, str, bytes]:
    """Returns (safe filename, extension, bytes) or raises DocumentError."""
    if file_storage is None or not file_storage.filename:
        raise DocumentError("Choose a file to upload.")
    original = os.path.basename(file_storage.filename.replace("\\", "/"))
    safe = secure_filename(original) or "file"
    ext = safe.rsplit(".", 1)[-1].lower() if "." in safe else ""
    if ext not in ALLOWED:
        raise DocumentError("That file type isn't allowed. Upload PDF, Word, Excel, PowerPoint, CSV, text, PNG or JPG files.")
    data = file_storage.read()
    max_bytes = current_app.config["MAX_UPLOAD_MB"] * 1024 * 1024
    if not data:
        raise DocumentError("The file is empty.")
    if len(data) > max_bytes:
        raise DocumentError(f"Files can be at most {current_app.config['MAX_UPLOAD_MB']} MB.")
    if not _looks_like(ext, data):
        raise DocumentError(f"This file doesn't look like a real .{ext} file, so it was refused for safety.")
    return safe[:200], ext, data


def storage_used_bytes(org_id=None) -> int:
    stmt = select(func.coalesce(func.sum(DocumentVersion.size_bytes), 0))
    if org_id:
        stmt = stmt.where(DocumentVersion.organization_id == org_id)
    return int(db.session.scalar(stmt) or 0)


def _check_space(org, adding: int) -> None:
    try:
        ensure_within_limit(org, "storage_mb", adding=max(1, -(-adding // (1024 * 1024))))
    except (LimitExceeded, SubscriptionInactive) as exc:
        raise DocumentError(f"{exc} Delete old versions or upgrade your plan.") from exc


def _store_version(actor: User, doc: Document, file_storage, note: str) -> DocumentVersion:
    name, ext, data = validate_upload(file_storage)
    _check_space(actor.organization, len(data))
    key = new_key(actor.organization_id)
    get_storage().put(key, data, ALLOWED[ext])
    number = (max((v.version_no for v in doc.versions), default=0)) + 1
    v = DocumentVersion(organization_id=actor.organization_id, document=doc, version_no=number, filename=name,
                        extension=ext, content_type=ALLOWED[ext], size_bytes=len(data),
                        sha256=hashlib.sha256(data).hexdigest(), storage_key=key, note=(note or "").strip()[:255],
                        uploaded_by_id=actor.id)
    db.session.add(v)
    return v


# ---------------------------------------------------------------- actions
def _default_title(file_storage) -> str:
    name = os.path.basename((getattr(file_storage, "filename", "") or "").replace("\\", "/"))
    return (os.path.splitext(name)[0].strip() or "Untitled document")[:200]


def upload(actor: User, *, project: Project | None, title: str, category: str, description: str, file_storage,
           note: str = "") -> Document:
    if not can_upload(actor, project):
        raise DocumentError("You can't upload documents here.")
    if project is not None and not project.is_open:
        raise DocumentError("This project is closed.")
    if category not in dict(DOCUMENT_CATEGORIES):
        raise DocumentError("Choose a category.")
    doc = Document(organization_id=actor.organization_id, project_id=project.id if project else None,
                   title=(title or "").strip()[:200] or _default_title(file_storage), category=category,
                   description=(description or "").strip(), created_by_id=actor.id)
    db.session.add(doc)
    db.session.flush()
    v = _store_version(actor, doc, file_storage, note or "First version")
    db.session.flush()
    audit.record("document.uploaded", summary=f"Uploaded “{doc.title}” ({v.filename})", entity=doc)
    if project is not None:
        notify([m.user_id for m in project.members], "document",
               f"{actor.full_name} added “{doc.title}” to {project.name}", f"/app/documents/{doc.id}",
               actor=actor)
    return doc


def add_version(actor: User, doc: Document, file_storage, note: str) -> DocumentVersion:
    if not (can_manage(actor, doc) or can_upload(actor, doc.project)):
        raise DocumentError("You can't add versions to this document.")
    if doc.is_archived:
        raise DocumentError("Restore the document before adding a version.")
    v = _store_version(actor, doc, file_storage, note)
    db.session.flush()
    audit.record("document.version", summary=f"Version {v.version_no} of “{doc.title}”", entity=doc)
    watchers = {doc.created_by_id} | {vv.uploaded_by_id for vv in doc.versions}
    notify([w for w in watchers if w], "document", f"New version {v.version_no} of “{doc.title}”",
           f"/app/documents/{doc.id}", actor=actor)
    return v


def set_archived(actor: User, doc: Document, archived: bool) -> None:
    if not can_manage(actor, doc):
        raise DocumentError("You can't change this document.")
    doc.is_archived = archived
    audit.record("document.archived" if archived else "document.restored",
                 summary=f"{'Archived' if archived else 'Restored'} “{doc.title}”", entity=doc)


def delete(actor: User, doc: Document) -> None:
    if not can_manage(actor, doc):
        raise DocumentError("You can't delete this document.")
    keys = [v.storage_key for v in doc.versions]
    audit.record("document.deleted", summary=f"Deleted “{doc.title}” and {len(keys)} version(s)", entity=doc)
    db.session.delete(doc)
    db.session.flush()
    store = get_storage()
    for k in keys:
        store.delete(k)


def delete_version(actor: User, v: DocumentVersion) -> None:
    doc = v.document
    if not can_manage(actor, doc):
        raise DocumentError("You can't change this document.")
    if len(doc.versions) == 1:
        raise DocumentError("This is the only version. Delete the document instead.")
    key = v.storage_key
    audit.record("document.version_deleted", summary=f"Deleted version {v.version_no} of “{doc.title}”", entity=doc)
    db.session.delete(v)
    db.session.flush()
    get_storage().delete(key)


def read(actor: User, v: DocumentVersion) -> bytes:
    if not can_view(actor, v.document):
        raise DocumentError("You can't open this document.")
    data = get_storage().get(v.storage_key)
    if hashlib.sha256(data).hexdigest() != v.sha256:
        raise DocumentError("This file failed its integrity check and was not served.")
    audit.record("document.downloaded", summary=f"Downloaded “{v.document.title}” v{v.version_no}",
                 entity=v.document)
    return data


def comment(actor: User, doc: Document, body: str) -> DocumentComment:
    if not can_view(actor, doc):
        raise DocumentError("You can't comment here.")
    if not body.strip():
        raise DocumentError("Write a comment first.")
    c = DocumentComment(organization_id=doc.organization_id, document_id=doc.id, author_id=actor.id, body=body.strip())
    db.session.add(c)
    notify([doc.created_by_id] if doc.created_by_id else [], "comment",
           f"{actor.full_name} commented on “{doc.title}”", f"/app/documents/{doc.id}", actor=actor)
    return c
