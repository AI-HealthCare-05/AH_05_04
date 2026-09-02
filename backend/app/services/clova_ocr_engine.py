import asyncio
import json
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from app.core.provider_observability import (
    ProviderCallContext,
    ProviderCallDescriptor,
    ProviderCallLogger,
    ProviderCallObserver,
    ProviderCallSpan,
    ProviderErrorCode,
    ProviderFailurePhase,
    provider_call_logger,
)
from app.services.ocr_ai import OcrStructurer
from app.services.ocr_engine import (
    OcrDeadline,
    OcrDeadlineExceededError,
    OcrProcessingError,
    OcrProviderConnectionError,
    OcrProviderTimeoutError,
    OcrProviderUnavailableError,
    OcrRecognitionResult,
    RawRecognizedField,
)


class ClovaOcrEngine:
    _SUPPORTED_FORMATS = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "application/pdf": "pdf",
    }

    def __init__(
        self,
        *,
        invoke_url: str,
        secret_key: str,
        storage_dir: str,
        timeout_seconds: float,
        structurer: OcrStructurer,
        client: httpx.AsyncClient | None = None,
        context: ProviderCallContext | None = None,
        descriptor: ProviderCallDescriptor | None = None,
        call_logger: ProviderCallLogger = provider_call_logger,
        observability_disabled: bool = False,
    ) -> None:
        self._invoke_url = invoke_url
        self._secret_key = secret_key
        self._storage_dir = Path(storage_dir).resolve()
        self._timeout_seconds = timeout_seconds
        self._client = client
        self._observer = ProviderCallObserver(
            context=context,
            descriptor=descriptor,
            call_logger=call_logger,
            observability_disabled=observability_disabled,
        )
        # 설정에 따라 규칙 기반 또는 LLM 구조화기를 주입받습니다.
        self._structurer = structurer

    async def recognize(
        self,
        *,
        object_key: str,
        file_mime_type: str,
        deadline: OcrDeadline,
    ) -> OcrRecognitionResult:
        # CLOVA 호출 직전 남은 예산을 다시 계산합니다.
        clova_timeout = deadline.timeout_for(self._timeout_seconds)
        if clova_timeout <= 0:
            raise OcrDeadlineExceededError("OCR 요청 예산이 남아 있지 않습니다.")

        image_format = self._SUPPORTED_FORMATS.get(file_mime_type)
        if image_format is None:
            raise OcrProcessingError("지원하지 않는 OCR 파일 형식입니다.")

        file_path = self._resolve_file_path(object_key)

        try:
            file_content = await asyncio.to_thread(file_path.read_bytes)
        except OSError as error:
            raise OcrProcessingError("OCR 대상 파일을 읽을 수 없습니다.") from error

        message = {
            "version": "V2",
            "requestId": str(uuid4()),
            "timestamp": int(time.time() * 1000),
            "lang": "ko",
            "images": [
                {
                    "format": image_format,
                    "name": file_path.stem,
                }
            ],
        }
        request_id = str(message["requestId"])
        span = self._observer.start(requested_model=None, provider_request_id=request_id)
        parsed_result = await self._recognize_provider(
            span=span,
            request_id=request_id,
            file_name=file_path.name,
            file_content=file_content,
            file_mime_type=file_mime_type,
            message=message,
            clova_timeout=clova_timeout,
        )

        # 구조화기 호출 직전 남은 예산을 다시 확인합니다.
        # 예산이 없으면 Provider를 호출하지 않고 종료합니다.
        if deadline.remaining() <= 0:
            raise OcrDeadlineExceededError("OCR 요청 예산이 남아 있지 않습니다.")

        # CLOVA 전체 token을 설정에서 선택한 구조화기에 전달합니다.
        structured_result = await self._structurer.structure(parsed_result.raw_fields)

        return OcrRecognitionResult(
            raw_text=parsed_result.raw_text,
            raw_fields=parsed_result.raw_fields,
            fields=structured_result.fields,
            # 규칙 기반 경로에서는 model/prompt가 null이고,
            # LLM이 실제 실행된 경우에만 해당 실행 정보를 기록합니다.
            engine_name="CLOVA_OCR",
            model_version=structured_result.model_name,
            prompt_version=structured_result.prompt_version,
        )

    async def _recognize_provider(
        self,
        *,
        span: ProviderCallSpan | None,
        request_id: str,
        file_name: str,
        file_content: bytes,
        file_mime_type: str,
        message: dict[str, Any],
        clova_timeout: float,
    ) -> OcrRecognitionResult:
        response: httpx.Response | None = None
        try:
            response = await self._request(
                file_name=file_name,
                file_content=file_content,
                file_mime_type=file_mime_type,
                message=message,
                clova_timeout=clova_timeout,
            )
            if response.status_code == 429:
                self._observer.failed(
                    span,
                    ProviderFailurePhase.HTTP_STATUS,
                    ProviderErrorCode.PROVIDER_RATE_LIMITED,
                    provider_request_id=request_id,
                    provider_response_received=True,
                    http_status=response.status_code,
                )
                raise OcrProviderUnavailableError("CLOVA OCR 서비스를 사용할 수 없습니다.")
            if response.status_code >= 500:
                self._observer.failed(
                    span,
                    ProviderFailurePhase.HTTP_STATUS,
                    ProviderErrorCode.PROVIDER_UNAVAILABLE,
                    provider_request_id=request_id,
                    provider_response_received=True,
                    http_status=response.status_code,
                )
                raise OcrProviderUnavailableError("CLOVA OCR 서비스를 사용할 수 없습니다.")
            if response.status_code >= 400:
                self._observer.failed(
                    span,
                    ProviderFailurePhase.HTTP_STATUS,
                    ProviderErrorCode.PROVIDER_REQUEST_REJECTED,
                    provider_request_id=request_id,
                    provider_response_received=True,
                    http_status=response.status_code,
                )
                raise OcrProcessingError("CLOVA OCR 요청이 거부되었습니다.")

            # CLOVA 응답을 전체 raw token으로 변환합니다.
            parsed_result = self._parse_response(response)
        except asyncio.CancelledError:
            self._observer.failed(
                span,
                ProviderFailurePhase.APPLICATION_DEADLINE,
                ProviderErrorCode.PROVIDER_CALL_ABORTED,
                provider_request_id=request_id,
            )
            raise
        except OcrProviderTimeoutError:
            self._observer.failed(
                span,
                ProviderFailurePhase.TRANSPORT_TIMEOUT,
                ProviderErrorCode.PROVIDER_TIMEOUT,
                provider_request_id=request_id,
            )
            raise
        except OcrProviderConnectionError:
            self._observer.failed(
                span,
                ProviderFailurePhase.TRANSPORT_CONNECTION,
                ProviderErrorCode.PROVIDER_CONNECTION_FAILED,
                provider_request_id=request_id,
            )
            raise
        except (OcrProviderUnavailableError, OcrProcessingError):
            if span is not None and not span.terminal_emitted:
                self._observer.failed(
                    span,
                    ProviderFailurePhase.RESPONSE_VALIDATION,
                    ProviderErrorCode.PROVIDER_RESPONSE_INVALID,
                    provider_request_id=request_id,
                    provider_response_received=response is not None,
                    http_status=response.status_code if response is not None else None,
                )
            raise
        except Exception:
            self._observer.failed(
                span,
                ProviderFailurePhase.UNKNOWN_INTERNAL,
                ProviderErrorCode.PROVIDER_INTERNAL_FAILURE,
                provider_request_id=request_id,
                provider_response_received=response is not None,
                http_status=response.status_code if response is not None else None,
            )
            raise
        self._observer.succeeded(
            span,
            model_name=None,
            provider_request_id=request_id,
            provider_response_received=True,
            http_status=response.status_code,
        )
        return parsed_result

    def _resolve_file_path(self, object_key: str) -> Path:
        file_path = (self._storage_dir / object_key).resolve()

        if not file_path.is_relative_to(self._storage_dir):
            raise OcrProcessingError("OCR 대상 파일 경로가 올바르지 않습니다.")

        if not file_path.is_file():
            raise OcrProcessingError("OCR 대상 파일이 존재하지 않습니다.")

        return file_path

    async def _request(
        self,
        *,
        file_name: str,
        file_content: bytes,
        file_mime_type: str,
        message: dict[str, Any],
        clova_timeout: float,
    ) -> httpx.Response:
        headers = {
            "X-OCR-SECRET": self._secret_key,
        }
        files: dict[
            str,
            tuple[str | None, bytes | str, str | None],
        ] = {
            "file": (file_name, file_content, file_mime_type),
            "message": (
                None,
                json.dumps(message, ensure_ascii=False),
                "application/json",
            ),
        }

        try:
            if self._client is not None:
                response = await self._client.post(
                    self._invoke_url,
                    headers=headers,
                    files=files,
                    timeout=clova_timeout,
                )
            else:
                async with httpx.AsyncClient(
                    timeout=clova_timeout,
                ) as client:
                    response = await client.post(
                        self._invoke_url,
                        headers=headers,
                        files=files,
                    )

        except httpx.TimeoutException as error:
            raise OcrProviderTimeoutError("CLOVA OCR 응답 제한시간을 초과했습니다.") from error
        except httpx.RequestError as error:
            raise OcrProviderConnectionError("CLOVA OCR 연결에 실패했습니다.") from error

        return response

    def _parse_response(
        self,
        response: httpx.Response,
    ) -> OcrRecognitionResult:
        try:
            payload = response.json()
            images = payload["images"]
            image = images[0]

            if image["inferResult"] != "SUCCESS":
                raise OcrProcessingError("CLOVA OCR 이미지 인식에 실패했습니다.")

            clova_fields = image["fields"]
        except OcrProcessingError:
            raise
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise OcrProcessingError("CLOVA OCR 응답 형식이 올바르지 않습니다.") from error

        raw_fields: list[RawRecognizedField] = []

        for clova_field in clova_fields:
            try:
                raw_value = clova_field["inferText"]
                confidence = clova_field.get("inferConfidence")
                vertices = clova_field["boundingPoly"]["vertices"]

                if not isinstance(raw_value, str):
                    raise TypeError

                if confidence is not None and not isinstance(
                    confidence,
                    int | float,
                ):
                    raise TypeError

                if not isinstance(vertices, list) or not vertices:
                    raise TypeError

                x_values = [float(vertex["x"]) for vertex in vertices]
                y_values = [float(vertex["y"]) for vertex in vertices]

                center_x = sum(x_values) / len(x_values)
                center_y = sum(y_values) / len(y_values)
                height = max(y_values) - min(y_values)
            except (
                KeyError,
                TypeError,
                ValueError,
                ZeroDivisionError,
            ) as error:
                raise OcrProcessingError("CLOVA OCR 필드를 변환할 수 없습니다.") from error

            raw_fields.append(
                RawRecognizedField(
                    raw_value=raw_value,
                    confidence_score=(float(confidence) if confidence is not None else None),
                    center_x=center_x,
                    center_y=center_y,
                    height=height,
                )
            )

        raw_fields.sort(
            key=lambda field: (
                round(field.center_y / 12),
                field.center_x,
            )
        )

        raw_text = " ".join(field.raw_value for field in raw_fields)

        return OcrRecognitionResult(
            raw_text=raw_text,
            raw_fields=raw_fields,
            # 비동기 LLM 구조화는 recognize()에서 수행합니다.
            fields=[],
        )
