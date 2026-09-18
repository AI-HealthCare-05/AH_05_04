from io import BytesIO

from PIL import Image


def synthetic_jpeg() -> bytes:
    output = BytesIO()
    Image.new("RGB", (16, 12), "white").save(output, format="JPEG")
    return output.getvalue()
