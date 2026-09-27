"""The image host (``img`` service) and the player photo API."""

from __future__ import annotations

import base64
from datetime import UTC, datetime

from flask import Flask, current_app, jsonify, request

from ...auth import require_account
from ...extensions import db
from ...models import Account, PlayerImage, StoredImage
from .image_store import (
    IMMUTABLE_CACHE,
    default_image_path,
    image_response,
    release_image,
    room_image_path,
    save_image,
    stored_image_path,
)
from .imaging import ImageRejected, ProcessedImage, process_upload


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def public_image_url(name: str) -> str:
    domain = current_app.config["RECNET_DOMAIN"]
    scheme = "http" if domain.startswith("localhost") else "https"
    host = domain if current_app.config["SINGLE_HOST_MODE"] else f"img.{domain}"
    return f"{scheme}://{host}/{name}"


def read_uploaded_image(maximum: int) -> tuple[ProcessedImage | None, tuple | None]:
    """Return the processed first uploaded file, or an error response tuple."""

    uploaded = next(iter(request.files.values()), None)
    if uploaded is None:
        return None, (jsonify(error="missing image"), 400)
    content = uploaded.stream.read(maximum + 1)
    if len(content) > maximum:
        return None, (jsonify(error="image too large", maxBytes=maximum), 413)
    try:
        processed = process_upload(content, max_pixels=current_app.config["IMAGE_MAX_PIXELS"])
    except ImageRejected as error:
        return None, (jsonify(error=f"{error}; image must be JPEG, PNG, or WebP"), 400)
    return processed, None


def stored_image_dto(image: StoredImage) -> dict:
    return {
        "ImageName": image.name,
        "Url": public_image_url(image.name),
        "OwnerAccountId": image.owner_account_id,
        "ContentType": image.content_type,
        "Width": image.width,
        "Height": image.height,
        "SizeBytes": image.size_bytes,
        "Sha256": image.sha256,
        "CreatedAt": _iso(image.created_at),
    }


def player_image_dto(image: PlayerImage) -> dict:
    return {
        "Id": image.id,
        "Type": 1,
        "Accessibility": 1,
        "AccessibilityLocked": False,
        "PlayerId": image.account_id,
        "ImageName": image.image_name,
        "Url": public_image_url(image.image_name) if image.image_name else "",
        "RoomId": image.room_id,
        "PlayerEventId": None,
        "TaggedPlayerIds": [],
        "Caption": image.caption,
        "Description": image.caption,
        "CheerCount": 0,
        "CommentCount": 0,
        "CreatedAt": _iso(image.created_at),
    }


def _request_ids() -> list[int]:
    ids: list[int] = []
    for value in request.args.getlist("id") + request.args.getlist("ids"):
        ids.extend(int(item) for item in value.split(",") if item.strip().isdigit())
    return ids


def _optional_int(value) -> int | None:
    return int(value) if str(value or "").strip().isdigit() else None


def register_image_routes(app: Flask) -> None:
    @app.get("/<image_name>.<any(jpg, jpeg, png, webp):extension>")
    def public_image(image_name: str, extension: str):
        filename = f"{image_name}.{extension}"
        path = stored_image_path(filename) or room_image_path(filename)
        cache_control = IMMUTABLE_CACHE
        if path is None:
            path = default_image_path()
            cache_control = "public, max-age=300"
        response = image_response(path, cache_control=cache_control)
        if request.args.get("sig") == "p1" and response.status_code < 400:
            placeholder = base64.b64encode(bytes(256)).decode("ascii")
            response.headers["Content-Signature"] = f"key-id=KEY:RSA:p1.rec.net; data={placeholder}"
        return response

    @app.get("/api/images/v1/metadata/<image_name>")
    def image_metadata(image_name: str):
        image = db.session.get(StoredImage, image_name)
        if image is None:
            return jsonify(error="not_found"), 404
        return jsonify(stored_image_dto(image))

    @app.post("/api/images/v1/upload")
    @require_account
    def upload_image(account: Account):
        processed, error = read_uploaded_image(current_app.config["IMAGE_MAX_BYTES"])
        if error is not None:
            return error
        image = save_image(processed, account)
        db.session.commit()
        return jsonify(stored_image_dto(image)), 201

    @app.post("/api/images/v4/uploadsaved")
    @require_account
    def upload_saved_image(account: Account):
        body = request.form if request.form or request.files else (
            request.get_json(silent=True) or {}
        )
        image_name = str(body.get("ImageName", body.get("imageName", ""))).strip()
        if request.files:
            processed, error = read_uploaded_image(current_app.config["IMAGE_MAX_BYTES"])
            if error is not None:
                return error
            image_name = save_image(processed, account).name
        if not image_name:
            return jsonify(error="missing image or ImageName"), 400
        photo = PlayerImage(
            account_id=account.id,
            image_name=image_name,
            room_id=_optional_int(body.get("RoomId", body.get("roomId"))),
            caption=str(body.get("Caption", body.get("Description", ""))),
        )
        db.session.add(photo)
        db.session.commit()
        return jsonify(player_image_dto(photo))

    @app.get("/api/images/v2/named")
    def named_images_v2():
        return jsonify([])

    @app.get("/api/images/v5/player/<int:player_id>")
    @app.get("/api/images/v4/player/<int:player_id>")
    def player_images(player_id: int):
        rows = db.session.scalars(
            db.select(PlayerImage)
            .where(PlayerImage.account_id == player_id)
            .order_by(PlayerImage.id.desc())
        ).all()
        return jsonify([player_image_dto(row) for row in rows])

    @app.get("/api/images/v1/room/<int:room_id>")
    def room_images(room_id: int):
        rows = db.session.scalars(
            db.select(PlayerImage)
            .where(PlayerImage.room_id == room_id)
            .order_by(PlayerImage.id.desc())
        ).all()
        return jsonify([player_image_dto(row) for row in rows])

    @app.get("/api/images/v5/bulk")
    def bulk_images_v5():
        ids = _request_ids()
        images = (
            db.session.scalars(db.select(PlayerImage).where(PlayerImage.id.in_(ids))).all()
            if ids
            else []
        )
        return jsonify([player_image_dto(image) for image in images])

    @app.get("/api/images/v1/<int:image_id>")
    def player_image(image_id: int):
        photo = db.session.get(PlayerImage, image_id)
        if photo is None:
            return jsonify(error="not_found"), 404
        return jsonify(player_image_dto(photo))

    @app.delete("/api/images/v1/<int:image_id>")
    @require_account
    def delete_player_image(account: Account, image_id: int):
        photo = db.session.get(PlayerImage, image_id)
        if photo is None:
            return jsonify(error="not_found"), 404
        if photo.account_id != account.id and not (account.is_moderator or account.is_developer):
            return jsonify(error="forbidden"), 403
        image_name = photo.image_name
        db.session.delete(photo)
        db.session.flush()
        unused = release_image(image_name)
        db.session.commit()
        if unused is not None:
            unused.unlink(missing_ok=True)
        return "", 204
