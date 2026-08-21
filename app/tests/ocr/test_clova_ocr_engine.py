import json
from pathlib import Path

import httpx
import pytest

from app.services.clova_ocr_engine import ClovaOcrEngine
from app.services.ocr_engine import (
    OcrProcessingError,
    OcrProviderConnectionError,
    OcrProviderTimeoutError,
    OcrProviderUnavailableError,
)


def _create_test_image(tmp_path: Path) -> None:
    (tmp_path / "sample.png").write_bytes(b"fake-png-content")


def _create_test_pdf(tmp_path: Path) -> None:
    (tmp_path / "sample.pdf").write_bytes(b"%PDF-1.7 test")


def _create_engine(
    *,
    tmp_path: Path,
    client: httpx.AsyncClient,
) -> ClovaOcrEngine:
    return ClovaOcrEngine(
        invoke_url="https://example.com/clova-ocr",
        secret_key="test-secret",
        storage_dir=str(tmp_path),
        timeout_seconds=20.0,
        client=client,
    )


async def test_recognize_calls_clova_and_parses_v2_response(
    tmp_path: Path,
) -> None:
    _create_test_image(tmp_path)

    async def handler(request: httpx.Request) -> httpx.Response:
        body = await request.aread()

        assert request.method == "POST"
        assert request.headers["X-OCR-SECRET"] == "test-secret"
        assert "multipart/form-data" in request.headers["Content-Type"]
        assert b"sample.png" in body
        assert b'"version": "V2"' in body
        assert b'"lang": "ko"' in body

        return httpx.Response(
            200,
            json={
                "images": [
                    {
                        "inferResult": "SUCCESS",
                        "message": "SUCCESS",
                        "fields": [
                            {
                                "inferText": "처방전",
                                "inferConfidence": 0.99,
                                "lineBreak": False,
                                "boundingPoly": {
                                    "vertices": [
                                        {"x": 10, "y": 10},
                                        {"x": 60, "y": 10},
                                        {"x": 60, "y": 20},
                                        {"x": 10, "y": 20},
                                    ]
                                },
                            },
                            {
                                "inferText": "발행일자",
                                "inferConfidence": 0.98,
                                "lineBreak": True,
                                "boundingPoly": {
                                    "vertices": [
                                        {"x": 70, "y": 10},
                                        {"x": 130, "y": 10},
                                        {"x": 130, "y": 20},
                                        {"x": 70, "y": 20},
                                    ]
                                },
                            },
                            {
                                "inferText": "2026-08-12",
                                "inferConfidence": 0.97,
                                "lineBreak": False,
                                "boundingPoly": {
                                    "vertices": [
                                        {"x": 10, "y": 40},
                                        {"x": 100, "y": 40},
                                        {"x": 100, "y": 50},
                                        {"x": 10, "y": 50},
                                    ]
                                },
                            },
                        ],
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as client:
        engine = _create_engine(
            tmp_path=tmp_path,
            client=client,
        )

        result = await engine.recognize(
            object_key="sample.png",
            file_mime_type="image/png",
        )

    assert result.raw_text == "처방전 발행일자 2026-08-12"
    assert len(result.raw_fields) == 3
    assert result.raw_fields[0].center_x == 35.0
    assert result.raw_fields[0].center_y == 15.0
    assert result.raw_fields[2].center_x == 55.0
    assert result.raw_fields[2].center_y == 45.0
    assert result.raw_fields[0].raw_value == "처방전"
    assert result.raw_fields[0].confidence_score == 0.99
    assert result.raw_fields[2].raw_value == "2026-08-12"
    assert result.raw_fields[2].confidence_score == 0.97
    assert result.raw_fields[0].height == 10.0
    assert result.raw_fields[2].height == 10.0
    assert len(result.fields) == 1

    prescribed_date = result.fields[0]

    assert prescribed_date.medication_index == 0
    assert prescribed_date.field_type == "PRESCRIBED_DATE"
    assert prescribed_date.raw_value == "2026-08-12"
    assert prescribed_date.confidence_score == 0.97


async def test_recognize_sends_pdf_format_to_clova(
    tmp_path: Path,
) -> None:
    _create_test_pdf(tmp_path)

    async def handler(request: httpx.Request) -> httpx.Response:
        body = await request.aread()

        assert b"sample.pdf" in body
        assert b'"format": "pdf"' in body
        assert b"application/pdf" in body

        return httpx.Response(
            200,
            json={
                "images": [
                    {
                        "inferResult": "SUCCESS",
                        "fields": [],
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as client:
        engine = _create_engine(
            tmp_path=tmp_path,
            client=client,
        )

        result = await engine.recognize(
            object_key="sample.pdf",
            file_mime_type="application/pdf",
        )

    assert result.raw_text == ""
    assert result.raw_fields == []


async def test_recognize_converts_timeout_error(
    tmp_path: Path,
) -> None:
    _create_test_image(tmp_path)

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(
            "timeout",
            request=request,
        )

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as client:
        engine = _create_engine(
            tmp_path=tmp_path,
            client=client,
        )

        with pytest.raises(OcrProviderTimeoutError):
            await engine.recognize(
                object_key="sample.png",
                file_mime_type="image/png",
            )


async def test_recognize_converts_connection_error(
    tmp_path: Path,
) -> None:
    _create_test_image(tmp_path)

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "connection failed",
            request=request,
        )

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as client:
        engine = _create_engine(
            tmp_path=tmp_path,
            client=client,
        )

        with pytest.raises(OcrProviderConnectionError):
            await engine.recognize(
                object_key="sample.png",
                file_mime_type="image/png",
            )


@pytest.mark.parametrize("status_code", [429, 500, 503])
async def test_recognize_converts_provider_unavailable_response(
    tmp_path: Path,
    status_code: int,
) -> None:
    _create_test_image(tmp_path)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            request=request,
        )

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as client:
        engine = _create_engine(
            tmp_path=tmp_path,
            client=client,
        )

        with pytest.raises(OcrProviderUnavailableError):
            await engine.recognize(
                object_key="sample.png",
                file_mime_type="image/png",
            )


async def test_recognize_rejects_invalid_clova_response(
    tmp_path: Path,
) -> None:
    _create_test_image(tmp_path)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={"images": []},
        )

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as client:
        engine = _create_engine(
            tmp_path=tmp_path,
            client=client,
        )

        with pytest.raises(OcrProcessingError):
            await engine.recognize(
                object_key="sample.png",
                file_mime_type="image/png",
            )


async def test_recognize_rejects_missing_file(
    tmp_path: Path,
) -> None:
    async with httpx.AsyncClient() as client:
        engine = _create_engine(
            tmp_path=tmp_path,
            client=client,
        )

        with pytest.raises(OcrProcessingError):
            await engine.recognize(
                object_key="missing.png",
                file_mime_type="image/png",
            )


async def test_recognize_structures_three_medications_from_clova_fixture(
    tmp_path: Path,
) -> None:
    _create_test_image(tmp_path)

    fixture_path = (
        Path(__file__).parents[1] / "fixtures" / "ocr" / "structuring" / "prescription_medication_rows.clova.json"
    )
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=payload,
            request=request,
        )

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as client:
        engine = _create_engine(
            tmp_path=tmp_path,
            client=client,
        )

        result = await engine.recognize(
            object_key="sample.png",
            file_mime_type="image/png",
        )

    medication_names = [field for field in result.fields if field.field_type == "MEDICATION_NAME"]

    assert [field.medication_index for field in medication_names] == [
        1,
        2,
        3,
    ]
    assert [field.raw_value for field in medication_names] == [
        "로수바스타틴칼숨정 10mg",
        "에제티미브정 10mg",
        "오메가-3-산에틸에스테르 90연질캡슐 1000mg",
    ]
