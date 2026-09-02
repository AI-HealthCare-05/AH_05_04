import json
from pathlib import Path

import httpx
import pytest

from app.services.clova_ocr_engine import ClovaOcrEngine
from app.services.ocr_ai import OcrStructureResult
from app.services.ocr_engine import (
    OcrDeadline,
    OcrDeadlineExceededError,
    OcrProcessingError,
    OcrProviderConnectionError,
    OcrProviderTimeoutError,
    OcrProviderUnavailableError,
    RawRecognizedField,
    RecognizedField,
)


def _test_deadline(total_seconds: float = 60.0) -> OcrDeadline:
    """테스트에서 예산이 충분한 deadline을 만듭니다."""
    return OcrDeadline.start(total_seconds=total_seconds, response_margin_seconds=0.0)


class RecordingStructurer:
    """실제 OpenAI를 호출하지 않고 CLOVA와 LLM 연결만 검증합니다."""

    def __init__(self) -> None:
        self.received_fields: list[RawRecognizedField] = []

    async def structure(
        self,
        raw_fields: list[RawRecognizedField],
    ) -> OcrStructureResult:
        self.received_fields = list(raw_fields)

        fields: list[RecognizedField] = []

        # 기존 테스트가 검사하는 처방일 필드만 가짜 결과로 반환합니다.
        for field in raw_fields:
            if field.raw_value == "2026-08-12":
                fields.append(
                    RecognizedField(
                        medication_index=0,
                        field_type="PRESCRIBED_DATE",
                        raw_value=field.raw_value,
                        normalized_value=field.raw_value,
                        normalization_version="date-rule-v1",
                        confidence_score=field.confidence_score,
                    )
                )

        return OcrStructureResult(
            fields=fields,
            model_name="ocr-structure-test-model",
            prompt_version="ocr-structure-prompt-v2",
        )


def _create_test_image(tmp_path: Path) -> None:
    (tmp_path / "sample.png").write_bytes(b"fake-png-content")


def _create_test_pdf(tmp_path: Path) -> None:
    (tmp_path / "sample.pdf").write_bytes(b"%PDF-1.7 test")


def _create_engine(
    *,
    tmp_path: Path,
    client: httpx.AsyncClient,
    structurer: RecordingStructurer | None = None,
) -> ClovaOcrEngine:
    return ClovaOcrEngine(
        invoke_url="https://example.com/clova-ocr",
        secret_key="test-secret",
        storage_dir=str(tmp_path),
        timeout_seconds=20.0,
        client=client,
        structurer=(structurer if structurer is not None else RecordingStructurer()),
        observability_disabled=True,
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
            deadline=_test_deadline(),
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
            deadline=_test_deadline(),
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
                deadline=_test_deadline(),
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
                deadline=_test_deadline(),
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
                deadline=_test_deadline(),
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
                deadline=_test_deadline(),
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
                deadline=_test_deadline(),
            )


async def test_recognize_passes_all_clova_tokens_to_llm_structurer(
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
    structurer = RecordingStructurer()

    async with httpx.AsyncClient(transport=transport) as client:
        engine = _create_engine(
            tmp_path=tmp_path,
            client=client,
            structurer=structurer,
        )

        result = await engine.recognize(
            object_key="sample.png",
            file_mime_type="image/png",
            deadline=_test_deadline(),
        )

    expected_token_count = len(payload["images"][0]["fields"])

    # 기존 정규식 파서가 아니라 CLOVA 전체 token이 전달됐는지 확인합니다.
    assert len(structurer.received_fields) == expected_token_count
    assert structurer.received_fields == result.raw_fields
    assert result.engine_name == "CLOVA_OCR"
    assert result.model_version == "ocr-structure-test-model"
    assert result.prompt_version == "ocr-structure-prompt-v2"


async def test_recognize_does_not_call_provider_when_deadline_is_exhausted(
    tmp_path: Path,
) -> None:
    """예산이 소진되면 HTTP 호출 없이 OcrDeadlineExceededError를 던집니다.

    파일은 정상이지만 남은 예산이 없으므로 Provider를 호출하지 않아야 합니다.
    """
    _create_test_image(tmp_path)

    called = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={}, request=request)

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as client:
        engine = _create_engine(
            tmp_path=tmp_path,
            client=client,
        )

        # 응답 여유가 전체 예산과 같으면 Provider 경로 예산이 0입니다.
        exhausted = OcrDeadline.start(total_seconds=5.0, response_margin_seconds=5.0)

        with pytest.raises(OcrDeadlineExceededError):
            await engine.recognize(
                object_key="sample.png",
                file_mime_type="image/png",
                deadline=exhausted,
            )

    assert called is False, "예산이 없는데 Provider를 호출했습니다."
