"""Decode and EXIF-normalize new raster uploads; never modify uploaded bytes."""

import warnings
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.errors import ApiError, ErrorDetail

MAX_IMAGE_PIXELS = 20_000_000
MAX_NORMALIZED_BYTES = 30 * 1024 * 1024


@dataclass(frozen=True)
class NormalizedImage:
    content: bytes
    width: int
    height: int


def normalize_document_image(content: bytes, mime_type: str) -> NormalizedImage:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as source:
                expected = {"image/jpeg": "JPEG", "image/png": "PNG"}.get(mime_type)
                if source.format != expected or source.width * source.height > MAX_IMAGE_PIXELS:
                    raise ValueError("Unsupported image")
                if getattr(source, "n_frames", 1) != 1:
                    raise ValueError("Animated image")
                source.load()
                oriented = ImageOps.exif_transpose(source)
                # Fresh image drops EXIF, ICC, comments and any other source metadata.
                mode = "RGBA" if "A" in oriented.getbands() or "transparency" in source.info else "RGB"
                pixels = oriented.convert(mode)
                clean = Image.new(mode, pixels.size)
                clean.paste(pixels)
                output = BytesIO()
                clean.save(output, format="PNG")
                result = output.getvalue()
                if len(result) > MAX_NORMALIZED_BYTES:
                    raise ValueError("Normalized image too large")
                return NormalizedImage(result, clean.width, clean.height)
    except (
        OSError,
        ValueError,
        SyntaxError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise ApiError(
            status_code=400,
            code="UPLOAD_FILE_INVALID_TYPE",
            message="이미지를 처리할 수 없습니다. 다른 JPG 또는 PNG 파일을 선택해 주세요.",
            details=[ErrorDetail(field="file", reason="INVALID_IMAGE")],
        ) from None
