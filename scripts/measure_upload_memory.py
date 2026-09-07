import asyncio
import gc
import tempfile
import tracemalloc
from typing import cast

from fastapi import UploadFile

from app.repositories.medical_document_repository import MedicalDocumentRepository
from app.services.medical_documents import (
    MAX_DOCUMENT_SIZE_BYTES,
    MedicalDocumentService,
)

MIB = 1024 * 1024


async def measure(concurrency: int) -> None:
    service = MedicalDocumentService(
        repository=cast(MedicalDocumentRepository, None),
    )
    uploads: list[UploadFile] = []

    try:
        # 측정 전에 합성 파일을 디스크에 준비합니다.
        for _ in range(concurrency):
            temporary_file = tempfile.TemporaryFile(mode="w+b")
            upload = UploadFile(
                filename="synthetic-upload.bin",
                file=temporary_file,
            )
            uploads.append(upload)

            remaining = MAX_DOCUMENT_SIZE_BYTES
            block = b"x" * MIB
            while remaining > 0:
                write_size = min(MIB, remaining)
                temporary_file.write(block[:write_size])
                remaining -= write_size

            temporary_file.seek(0)

        gc.collect()
        tracemalloc.start()

        try:
            # 결과를 함께 보유해 동시 요청의 버퍼가 겹치는 상황을 측정합니다.
            contents = await asyncio.gather(*(service._read_upload_content(file=upload) for upload in uploads))

            current, peak = tracemalloc.get_traced_memory()
            assert all(len(content) == MAX_DOCUMENT_SIZE_BYTES for content in contents)

            print(f"concurrency={concurrency} retained_mib={current / MIB:.2f} peak_mib={peak / MIB:.2f}")
            del contents
        finally:
            tracemalloc.stop()
    finally:
        for upload in uploads:
            await upload.close()


async def main() -> None:
    for concurrency in (1, 3, 6):
        await measure(concurrency)


if __name__ == "__main__":
    asyncio.run(main())
