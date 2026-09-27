import io
from pathlib import Path

import pytest
from PIL import Image

from rrserver import create_app
from rrserver.extensions import db
from rrserver.models import StoredImage


@pytest.fixture()
def app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'images.sqlite3'}",
            "JWT_SECRET": "test-secret-longer-than-thirty-two-bytes",
            "RECNET_DOMAIN": "play.example.test",
            "SINGLE_HOST_MODE": False,
            "ALLOW_PASSWORDLESS_ACCOUNTS": True,
            "RATE_LIMIT_ENABLED": False,
            "IMAGE_MAX_BYTES": 512 * 1024,
            "IMAGE_MAX_PIXELS": 2_000_000,
        }
    )


@pytest.fixture()
def client(app):
    return app.test_client()


def player_headers(client) -> dict:
    tokens = client.post(
        "/connect/token",
        data={"grant_type": "create_account", "password": "correct horse battery staple"},
    ).get_json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def image_bytes(size=(64, 48), image_format="PNG", color=(10, 120, 200), **options) -> bytes:
    buffer = io.BytesIO()
    mode = "RGBA" if image_format == "PNG" else "RGB"
    Image.new(mode, size, color).save(buffer, image_format, **options)
    return buffer.getvalue()


def decode(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def upload(client, headers, content: bytes, filename="picture.png", path="/api/images/v1/upload"):
    return client.post(
        path,
        headers=headers,
        data={"image": (io.BytesIO(content), filename)},
        content_type="multipart/form-data",
    )


def stored_file(app, name: str) -> Path:
    return Path(app.instance_path) / "uploads" / "image" / name


def test_upload_serves_original_and_resized_variants(client):
    headers = player_headers(client)
    response = upload(client, headers, image_bytes((400, 200)))
    assert response.status_code == 201
    uploaded = response.get_json()
    name = uploaded["ImageName"]
    assert name.endswith(".png") and len(name) == 36
    assert uploaded["Url"] == f"https://img.play.example.test/{name}"
    assert (uploaded["Width"], uploaded["Height"], uploaded["ContentType"]) == (
        400,
        200,
        "image/png",
    )
    assert client.get(f"/api/images/v1/metadata/{name}").get_json() == uploaded

    original = client.get(f"/{name}")
    assert original.status_code == 200
    assert original.mimetype == "image/png"
    assert "immutable" in original.headers["Cache-Control"]
    assert decode(original.data).size == (400, 200)

    assert decode(client.get(f"/{name}?width=100").data).size == (100, 50)
    assert decode(client.get(f"/{name}?height=20").data).size == (40, 20)
    assert decode(client.get(f"/{name}?width=100&height=10").data).size == (20, 10)
    assert decode(client.get(f"/{name}?cropSquare=true").data).size == (200, 200)
    assert decode(client.get(f"/{name}?width=64&cropSquare=true").data).size == (64, 64)
    assert decode(client.get(f"/{name}?width=5000").data).size == (400, 200)
    assert client.get(f"/{name}?width=abc").status_code == 400
    assert client.get(f"/{name}?width=0").status_code == 400

    variant = client.get(f"/{name}?width=100")
    cached = client.get(f"/{name}?width=100", headers={"If-None-Match": variant.headers["ETag"]})
    assert cached.status_code == 304


def test_uploads_are_validated_and_stripped_of_metadata(client, app):
    headers = player_headers(client)
    assert upload(client, {}, image_bytes()).status_code == 401
    rejected = upload(client, headers, b"definitely not an image", "notes.txt")
    assert rejected.status_code == 400
    assert "JPEG, PNG, or WebP" in rejected.get_json()["error"]
    gif = io.BytesIO()
    Image.new("RGB", (8, 8)).save(gif, "GIF")
    assert upload(client, headers, gif.getvalue(), "anim.gif").status_code == 400
    truncated = image_bytes((300, 300), "JPEG")[:200]
    assert upload(client, headers, truncated, "broken.jpg").status_code == 400
    huge = image_bytes((2000, 2000), "PNG")
    assert upload(client, headers, huge, "huge.png").status_code == 400
    noisy = io.BytesIO()
    Image.effect_noise((600, 600), 100).convert("RGB").save(noisy, "BMP")
    too_big = upload(client, headers, noisy.getvalue(), "big.bmp")
    assert too_big.status_code == 413

    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: rotate 90 degrees clockwise
    exif[0x010F] = "SecretCameraMaker"
    tagged = image_bytes((60, 20), "JPEG", exif=exif.tobytes())
    name = upload(client, headers, tagged, "photo.jpg").get_json()["ImageName"]
    stored = stored_file(app, name).read_bytes()
    assert b"SecretCameraMaker" not in stored
    image = decode(stored)
    assert image.format == "JPEG"
    assert image.size == (20, 60)
    assert not image.getexif()


def test_saved_photos_can_be_listed_and_deleted(client, app):
    owner = player_headers(client)
    stranger = player_headers(client)
    owner_id = client.get("/account/me", headers=owner).get_json()["accountId"]

    response = client.post(
        "/api/images/v4/uploadsaved",
        headers=owner,
        data={
            "image": (io.BytesIO(image_bytes(image_format="WEBP")), "shot.webp"),
            "RoomId": "2",
            "Description": "Rec Center selfie",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    photo = response.get_json()
    assert photo["ImageName"].endswith(".webp")
    assert photo["PlayerId"] == owner_id
    assert photo["RoomId"] == 2
    assert photo["Caption"] == photo["Description"] == "Rec Center selfie"
    assert client.get(f"/{photo['ImageName']}").mimetype == "image/webp"

    assert [p["Id"] for p in client.get(f"/api/images/v4/player/{owner_id}").get_json()] == [
        photo["Id"]
    ]
    assert client.get("/api/images/v1/room/2").get_json()[0]["Id"] == photo["Id"]
    assert client.get(f"/api/images/v1/{photo['Id']}").get_json() == photo
    assert client.get(f"/api/images/v5/bulk?ids={photo['Id']}").get_json() == [photo]
    assert client.post("/api/images/v4/uploadsaved", headers=owner, json={}).status_code == 400

    path = stored_file(app, photo["ImageName"])
    assert path.is_file()
    assert client.delete(f"/api/images/v1/{photo['Id']}", headers=stranger).status_code == 403
    assert client.delete(f"/api/images/v1/{photo['Id']}", headers=owner).status_code == 204
    assert client.get(f"/api/images/v1/{photo['Id']}").status_code == 404
    assert not path.exists()
    with app.app_context():
        assert db.session.get(StoredImage, photo["ImageName"]) is None


def test_room_thumbnails_and_missing_images_are_served(client):
    thumbnail = client.get("/RecCenter.jpg")
    assert thumbnail.status_code == 200
    assert thumbnail.mimetype == "image/jpeg"
    assert decode(client.get("/RecCenter.jpg?width=64&cropSquare=true").data).size == (64, 64)

    signed = client.get("/RecCenter.jpg?sig=p1")
    assert signed.headers["Content-Signature"].startswith("key-id=KEY:RSA:p1.rec.net; data=")

    missing = client.get(f"/{'0' * 32}.png")
    assert missing.status_code == 200
    assert missing.mimetype == "image/jpeg"
    assert missing.headers["Cache-Control"] == "public, max-age=300"
    assert decode(missing.data).size == (512, 512)
    assert client.get("/api/images/v1/metadata/unknown.png").status_code == 404


def test_legacy_upload_endpoint_stores_images_for_the_image_host(client):
    headers = player_headers(client)
    response = client.post(
        "/upload",
        headers=headers,
        data={"FileType": "3", "file": (io.BytesIO(image_bytes((32, 32))), "room.png")},
        content_type="multipart/form-data",
    )
    name = response.get_json()["filename"]
    assert "/" not in name and name.endswith(".png")
    assert decode(client.get(f"/{name}").data).size == (32, 32)
    assert client.get(f"/image/{name}").status_code == 200

    opaque = client.post(
        "/upload",
        headers=headers,
        data={"FileType": "3", "file": (io.BytesIO(b"raw texture"), "blob.bin")},
        content_type="multipart/form-data",
    ).get_json()["filename"]
    assert client.get(f"/image/{opaque}").data == b"raw texture"


def test_replacing_a_profile_photo_removes_the_old_file(client, app):
    headers = player_headers(client)
    first = upload(client, headers, image_bytes(), path="/account/me/profilephoto").get_json()
    second = upload(
        client, headers, image_bytes(color=(1, 2, 3)), path="/account/me/profilephoto"
    ).get_json()
    assert not stored_file(app, first["imageName"]).exists()
    assert stored_file(app, second["imageName"]).is_file()
    photo = client.get(f"/account/{second['accountId']}/profilephoto?width=16")
    assert decode(photo.data).size == (16, 12)
    assert client.get(f"/{second['imageName']}").status_code == 200
