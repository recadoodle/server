from flask import Flask, current_app, jsonify, request

from ...auth import require_account
from ...extensions import db
from ...models import Account
from .image_store import image_response, release_image, save_image
from .imaging import ImageRejected, process_upload
from .storage import safe_blob_path


def register_profile_photo_routes(app: Flask) -> None:
    @app.post("/account/me/profilephoto")
    @require_account
    def upload_profile_photo(account: Account):
        uploaded = next(iter(request.files.values()), None)
        if uploaded is None:
            return jsonify(error="missing photo"), 400
        maximum = current_app.config["PROFILE_PHOTO_MAX_BYTES"]
        content = uploaded.stream.read(maximum + 1)
        if len(content) > maximum:
            return jsonify(error="photo too large", maxBytes=maximum), 413
        try:
            processed = process_upload(content, max_pixels=current_app.config["IMAGE_MAX_PIXELS"])
        except ImageRejected:
            return jsonify(error="photo must be JPEG, PNG, or WebP"), 400
        previous = account.profile_image
        image = save_image(processed, account)
        account.profile_image = image.name
        db.session.flush()
        unused = release_image(previous) if previous else None
        db.session.commit()
        if unused is not None:
            unused.unlink(missing_ok=True)
        return jsonify(
            accountId=account.id,
            imageName=image.name,
            contentType=image.content_type,
            width=image.width,
            height=image.height,
            url=f"/account/{account.id}/profilephoto",
        )

    @app.get("/account/<int:account_id>/profilephoto")
    def profile_photo(account_id: int):
        account = db.session.get(Account, account_id)
        if account is None or not account.profile_image:
            return "", 404
        candidate = safe_blob_path("image", account.profile_image)
        if candidate is None or not candidate.is_file():
            return "", 404
        return image_response(candidate, cache_control="public, max-age=300")
