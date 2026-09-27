"""Persistent storage and HTTP delivery of uploaded images."""

from __future__ import annotations

import hashlib
import io
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from flask import current_app, jsonify, request, send_file

from ...catalog import ROOM_IMAGE_DIR
from ...extensions import db
from ...models import Account, PlayerImage, StoredImage
from .imaging import (
    EXTENSION_FORMATS,
    FORMATS,
    STORED_IMAGE_NAME,
    ProcessedImage,
    VariantCache,
    parse_transform,
    placeholder_image,
    render_variant,
)
from .storage import upload_root

IMMUTABLE_CACHE = "public, max-age=2592000, immutable"
DEFAULT_IMAGE_NAME = "DefaultProfileImage.jpg"


def image_dir() -> Path:
    path = upload_root() / "image"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_image(processed: ProcessedImage, owner: Account | None) -> StoredImage:
    """Write a processed image under a new random name. The caller commits the session."""

    name = f"{uuid.uuid4().hex}{processed.suffix}"
    destination = image_dir() / name
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.write_bytes(processed.content)
    os.replace(temporary, destination)
    record = StoredImage(
        name=name,
        owner_account_id=owner.id if owner else None,
        content_type=processed.mimetype,
        width=processed.width,
        height=processed.height,
        size_bytes=len(processed.content),
        sha256=processed.sha256,
    )
    db.session.add(record)
    return record


def stored_image_path(name: str) -> Path | None:
    if not STORED_IMAGE_NAME.fullmatch(name):
        return None
    candidate = image_dir() / name
    return candidate if candidate.is_file() else None


def room_image_path(name: str) -> Path | None:
    candidate = ROOM_IMAGE_DIR / name
    if candidate.parent != ROOM_IMAGE_DIR or not candidate.is_file():
        return None
    return candidate


def default_image_path() -> Path:
    bundled = ROOM_IMAGE_DIR / DEFAULT_IMAGE_NAME
    if bundled.is_file():
        return bundled
    generated = upload_root() / "defaults" / DEFAULT_IMAGE_NAME
    if not generated.is_file():
        generated.parent.mkdir(parents=True, exist_ok=True)
        temporary = generated.with_suffix(".part")
        temporary.write_bytes(placeholder_image())
        os.replace(temporary, generated)
    return generated


def release_image(name: str) -> Path | None:
    """Forget a stored image once no account or saved photo refers to it.

    Returns the file to delete after the caller commits, or ``None`` if it is still used.
    """

    record = db.session.get(StoredImage, name)
    if record is None:
        return None
    referenced = db.session.scalar(
        db.select(db.func.count()).select_from(Account).where(Account.profile_image == name)
    ) or db.session.scalar(
        db.select(db.func.count()).select_from(PlayerImage).where(PlayerImage.image_name == name)
    )
    if referenced:
        return None
    db.session.delete(record)
    return stored_image_path(name)


def _variant_cache() -> VariantCache:
    cache = current_app.extensions.get("image_variant_cache")
    if cache is None:
        cache = VariantCache(current_app.config["IMAGE_VARIANT_CACHE_BYTES"])
        current_app.extensions["image_variant_cache"] = cache
    return cache


def image_response(path: Path, *, cache_control: str = IMMUTABLE_CACHE):
    """Send an image file, resized when ``width``, ``height`` or ``cropSquare`` is given."""

    image_format = EXTENSION_FORMATS.get(path.suffix.lower(), "JPEG")
    mimetype = FORMATS[image_format][1]
    try:
        transform = parse_transform(request.args, current_app.config["IMAGE_MAX_DIMENSION"])
    except ValueError as error:
        return jsonify(error=str(error)), 400

    if transform.is_identity:
        response = send_file(path, mimetype=mimetype, conditional=True)
    else:
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size, transform)
        cache = _variant_cache()
        content = cache.get(key)
        if content is None:
            content = render_variant(path.read_bytes(), image_format, transform)
            cache.put(key, content)
        response = send_file(
            io.BytesIO(content),
            mimetype=mimetype,
            conditional=True,
            etag=hashlib.sha256(content).hexdigest()[:32],
            last_modified=datetime.fromtimestamp(stat.st_mtime, UTC),
        )
    response.headers["Cache-Control"] = cache_control
    return response
