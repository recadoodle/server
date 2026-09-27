from flask import Flask

from .blobs import register_blob_routes
from .images import register_image_routes
from .profile_photos import register_profile_photo_routes


def register_media_routes(app: Flask) -> None:
    register_blob_routes(app)
    register_profile_photo_routes(app)
    register_image_routes(app)
