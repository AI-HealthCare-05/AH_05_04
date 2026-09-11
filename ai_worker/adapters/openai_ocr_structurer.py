"""Worker 소유의 OpenAI client 수명과 공용 OCR 구조화 연결."""

from openai import AsyncOpenAI

from ocr_runtime.llm.client import OpenAIOcrStructureClient
from ocr_runtime.llm.prompt import PROMPT_VERSION
from ocr_runtime.llm.structurer import LlmPrescriptionStructurer
from ocr_runtime.structuring import OcrStructureResult
from provider_contracts.observability import Provider, ProviderCallContext, ProviderCallDescriptor, ProviderOperation
from provider_contracts.ocr import RawRecognizedField


class WorkerLlmPrescriptionStructurer:
    def __init__(self, *, api_key: str, model: str, timeout_seconds: float, context: ProviderCallContext) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._context = context

    async def structure(self, raw_fields: list[RawRecognizedField]) -> OcrStructureResult:
        # Worker attempt가 재시도를 소유한다. SDK 자체 재시도를 중첩하지 않는다.
        # 성공·실패·취소 모두 HTTP client를 닫는다.
        async with AsyncOpenAI(api_key=self._api_key, timeout=self._timeout_seconds, max_retries=0) as client:
            structurer = LlmPrescriptionStructurer(
                provider=OpenAIOcrStructureClient(
                    client,
                    context=self._context,
                    descriptor=ProviderCallDescriptor(
                        provider=Provider.OPENAI,
                        operation=ProviderOperation.OCR_STRUCTURING,
                        prompt_version=PROMPT_VERSION,
                    ),
                ),
                model=self._model,
                timeout_seconds=self._timeout_seconds,
            )
            return await structurer.structure(raw_fields)
