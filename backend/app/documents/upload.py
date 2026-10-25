"""Validation of uploaded files. Uploaded content is treated as untrusted data and never executed."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from PIL import Image, UnidentifiedImageError

from ..config import get_settings

DOC_EXTENSIONS = {".pdf": "pdf", ".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".tif": "tiff", ".tiff": "tiff", ".webp": "webp"}
IMAGE_EXTENSIONS = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".webp": "webp", ".tif": "tiff", ".tiff": "tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}

# PDF name tokens that indicate active or embedded content.
ACTIVE_CONTENT_TOKENS = {
    b"/JavaScript": "Embedded JavaScript",
    b"/JS": "JavaScript action",
    b"/OpenAction": "Automatic action on open",
    b"/AA": "Additional (event-triggered) actions",
    b"/Launch": "Launch action (opens external application)",
    b"/EmbeddedFile": "Embedded file attachment",
    b"/RichMedia": "Rich media content",
    b"/XFA": "XFA dynamic form",
    b"/SubmitForm": "Form submission action",
    b"/ImportData": "Data import action",
}


class UploadRejected(ValueError):
    pass


@dataclass
class InspectedFile:
    detected_type: str
    page_count: int | None = None
    encrypted: bool = False
    active_content: list[dict] = field(default_factory=list)
    width: int | None = None
    height: int | None = None
    metadata: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def safe_filename(name: str | None) -> str:
    name = (name or "upload").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^\w.\- ()]+", "_", name).strip(" .") or "upload"
    return name[:200]


def extension_of(name: str) -> str:
    m = re.search(r"(\.[A-Za-z0-9]{1,5})$", name)
    return m.group(1).lower() if m else ""


def sniff(data: bytes) -> str | None:
    head = data[:16]
    if data[:1024].lstrip().startswith(b"%PDF-") or b"%PDF-" in data[:1024]:
        return "pdf"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[4:8] == b"ftyp" or head[:4] == b"\x1aE\xdf\xa3" or (head[:4] == b"RIFF" and data[8:12] == b"AVI "):
        return "video"
    return None


def _scan_active_content(data: bytes) -> list[dict]:
    found = []
    for token, label in ACTIVE_CONTENT_TOKENS.items():
        # match whole PDF name tokens only (followed by a delimiter)
        if re.search(re.escape(token) + rb"[\s/<>\[\]()]", data):
            found.append({"token": token.decode(), "description": label})
    return found


def inspect_pdf(data: bytes, password: str | None = None) -> InspectedFile:
    import pymupdf

    s = get_settings()
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise UploadRejected("The PDF could not be opened; it appears to be corrupted or not a PDF.") from exc
    info = InspectedFile(detected_type="pdf")
    try:
        if doc.needs_pass:
            info.encrypted = True
            if password:
                if not doc.authenticate(password):
                    raise UploadRejected("The supplied PDF password is incorrect.")
            else:
                info.warnings.append("The PDF is password-protected; content cannot be processed without the password.")
                info.page_count = doc.page_count if doc.page_count else None
                return info
        elif doc.is_encrypted:
            info.encrypted = True  # encrypted with empty user password (permissions only)
        if doc.page_count == 0:
            raise UploadRejected("The PDF contains no pages.")
        if doc.page_count > s.max_pdf_pages:
            raise UploadRejected(f"The PDF has {doc.page_count} pages; the limit is {s.max_pdf_pages}.")
        info.page_count = doc.page_count
        # Touch every page to surface structural corruption early.
        for i in range(doc.page_count):
            try:
                doc.load_page(i).bound()
            except Exception as exc:  # noqa: BLE001
                raise UploadRejected(f"Page {i + 1} of the PDF is damaged and cannot be read.") from exc
        info.metadata = {k: v for k, v in (doc.metadata or {}).items() if v}
    finally:
        doc.close()
    info.active_content = _scan_active_content(data)
    return info


def inspect_image(data: bytes) -> InspectedFile:
    s = get_settings()
    Image.MAX_IMAGE_PIXELS = s.max_image_pixels
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
        with Image.open(io.BytesIO(data)) as im:
            fmt = (im.format or "").lower()
            w, h = im.size
            frames = getattr(im, "n_frames", 1)
            im.load()
    except Image.DecompressionBombError as exc:
        raise UploadRejected("The image dimensions exceed the permitted limit.") from exc
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise UploadRejected("The image could not be decoded; it appears to be corrupted.") from exc
    mapping = {"jpeg": "jpeg", "mpo": "jpeg", "png": "png", "tiff": "tiff", "webp": "webp"}
    if fmt not in mapping:
        raise UploadRejected("Unsupported image format.")
    info = InspectedFile(detected_type=mapping[fmt], width=w, height=h, page_count=frames if fmt == "tiff" else 1)
    if frames > 1 and fmt == "tiff":
        if frames > s.max_pdf_pages:
            raise UploadRejected("The TIFF contains too many pages.")
    return info


def validate_document_upload(filename: str, data: bytes, password: str | None = None) -> InspectedFile:
    s = get_settings()
    if not data:
        raise UploadRejected("The file is empty.")
    if len(data) > s.max_document_mb * 1024 * 1024:
        raise UploadRejected(f"The file exceeds the {s.max_document_mb} MB limit for documents.")
    ext = extension_of(filename)
    if ext not in DOC_EXTENSIONS:
        raise UploadRejected("Unsupported file type. Upload a PDF or an image (JPEG, PNG, TIFF, WebP).")
    kind = sniff(data)
    if kind is None or kind == "video":
        raise UploadRejected("The file content does not match a supported document format.")
    if kind != DOC_EXTENSIONS[ext]:
        raise UploadRejected(f"The file extension ({ext}) does not match its actual content ({kind}).")
    return inspect_pdf(data, password) if kind == "pdf" else inspect_image(data)


def validate_image_upload(filename: str, data: bytes) -> InspectedFile:
    s = get_settings()
    if not data:
        raise UploadRejected("The file is empty.")
    if len(data) > s.max_image_mb * 1024 * 1024:
        raise UploadRejected(f"The image exceeds the {s.max_image_mb} MB limit.")
    ext = extension_of(filename)
    if ext not in IMAGE_EXTENSIONS:
        raise UploadRejected("Unsupported image type. Upload JPEG, PNG, WebP or TIFF.")
    kind = sniff(data)
    if kind != IMAGE_EXTENSIONS[ext]:
        raise UploadRejected("The file content does not match its image extension.")
    return inspect_image(data)
