"""Image validation, metadata stripping and resizing for the image service."""

from __future__ import annotations

import hashlib
import io
import re
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock

from PIL import Image, ImageOps, UnidentifiedImageError

FORMATS = {
    "JPEG": (".jpg", "image/jpeg"),
    "PNG": (".png", "image/png"),
    "WEBP": (".webp", "image/webp"),
}
EXTENSION_FORMATS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}
STORED_IMAGE_NAME = re.compile(r"[0-9a-f]{32}\.(?:jpg|png|webp)")
TRUE_VALUES = {"1", "true", "yes", "on"}


class ImageRejected(ValueError):
    """Raised when uploaded bytes are not an acceptable image."""


@dataclass(frozen=True)
class ProcessedImage:
    content: bytes
    format: str
    width: int
    height: int

    @property
    def suffix(self) -> str:
        return FORMATS[self.format][0]

    @property
    def mimetype(self) -> str:
        return FORMATS[self.format][1]

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


@dataclass(frozen=True)
class Transform:
    width: int | None = None
    height: int | None = None
    crop_square: bool = False

    @property
    def is_identity(self) -> bool:
        return self.width is None and self.height is None and not self.crop_square


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in {"RGBA", "LA", "PA"} or "transparency" in image.info


def _normalize_mode(image: Image.Image) -> Image.Image:
    if image.mode in {"RGB", "RGBA", "L"}:
        return image
    return image.convert("RGBA" if _has_alpha(image) else "RGB")


def encode(image: Image.Image, image_format: str) -> bytes:
    """Encode without EXIF, text chunks or other metadata; only the ICC profile survives."""

    buffer = io.BytesIO()
    icc_profile = image.info.get("icc_profile")
    options = {"icc_profile": icc_profile} if icc_profile else {}
    if image_format == "JPEG":
        image = _normalize_mode(image)
        if image.mode == "RGBA":
            background = Image.new("RGB", image.size, (255, 255, 255))
            background.paste(image, mask=image.getchannel("A"))
            image = background
        image.save(buffer, "JPEG", quality=90, optimize=True, **options)
    elif image_format == "PNG":
        image.save(buffer, "PNG", **options)
    else:
        image = _normalize_mode(image)
        if image.mode == "L":
            image = image.convert("RGB")
        image.save(buffer, "WEBP", quality=90, method=4, **options)
    return buffer.getvalue()


def process_upload(content: bytes, *, max_pixels: int) -> ProcessedImage:
    """Validate an uploaded image and re-encode it to strip location and camera metadata."""

    try:
        with Image.open(io.BytesIO(content)) as probe:
            image_format = probe.format
            if image_format not in FORMATS:
                raise ImageRejected("unsupported image format")
            if probe.width * probe.height > max_pixels:
                raise ImageRejected("image dimensions too large")
            probe.verify()
        with Image.open(io.BytesIO(content)) as image:
            image.load()
            upright = ImageOps.exif_transpose(image)
            encoded = encode(upright, image_format)
            width, height = upright.size
    except ImageRejected:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError,
            ValueError) as error:
        raise ImageRejected("invalid image data") from error
    return ProcessedImage(encoded, image_format, width, height)


def parse_transform(args, max_dimension: int) -> Transform:
    """Read ``width``, ``height`` and ``cropSquare`` query parameters."""

    def dimension(name: str) -> int | None:
        raw = args.get(name)
        if raw in (None, ""):
            return None
        if not raw.isdigit() or int(raw) <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return min(int(raw), max_dimension)

    crop_square = str(args.get("cropSquare", args.get("cropsquare", ""))).lower() in TRUE_VALUES
    return Transform(dimension("width"), dimension("height"), crop_square)


def render_variant(source: bytes, image_format: str, transform: Transform) -> bytes:
    """Resize or square-crop an image. Images are never enlarged."""

    with Image.open(io.BytesIO(source)) as original:
        original.load()
        image = _normalize_mode(original)
        if transform.crop_square:
            side = min(image.size)
            requested = [value for value in (transform.width, transform.height) if value]
            target = min([side, *requested])
            image = ImageOps.fit(image, (target, target), Image.Resampling.LANCZOS)
        else:
            width, height = image.size
            scale = min(
                1.0,
                (transform.width / width) if transform.width else 1.0,
                (transform.height / height) if transform.height else 1.0,
            )
            if scale < 1.0:
                size = (max(1, round(width * scale)), max(1, round(height * scale)))
                image = image.resize(size, Image.Resampling.LANCZOS)
        if "icc_profile" in original.info:
            image.info["icc_profile"] = original.info["icc_profile"]
        return encode(image, image_format)


def placeholder_image(size: int = 512) -> bytes:
    return encode(Image.new("RGB", (size, size), (58, 63, 75)), "JPEG")


class VariantCache:
    """Small thread-safe LRU cache of resized images, bounded by total bytes."""

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self._entries: OrderedDict[tuple, bytes] = OrderedDict()
        self._size = 0
        self._lock = Lock()

    def get(self, key: tuple) -> bytes | None:
        with self._lock:
            value = self._entries.get(key)
            if value is not None:
                self._entries.move_to_end(key)
            return value

    def put(self, key: tuple, value: bytes) -> None:
        if len(value) > self.max_bytes:
            return
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._size -= len(previous)
            self._entries[key] = value
            self._size += len(value)
            while self._size > self.max_bytes:
                _key, evicted = self._entries.popitem(last=False)
                self._size -= len(evicted)
