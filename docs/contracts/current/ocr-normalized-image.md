# OCR 검수 정규화 이미지 — #809

이 계약은 신규 JPEG/PNG 업로드의 OCR 입력과 검수 좌표계를 통일한다. 배포 시 DB migration `809a2b3c4d5e`를 먼저 적용해야 한다. Frontend overlay 구현 및 배포 확인은 별도 작업이다.

## 저장 및 처리

- 업로드 원본 바이트와 원본 메타데이터는 보존한다.
- JPEG/PNG는 EXIF orientation(회전·반전)을 적용한 PNG를 별도 저장한다. 정규화본에는 원본 EXIF/ICC metadata를 복사하지 않는다.
- Backend와 Worker는 정규화본이 있으면 동일한 PNG를 OCR 입력으로 사용한다.
- PDF와 기존 문서는 정규화하지 않는다. 기존 문서의 자동 backfill은 없다.
- 입력은 기존 30 MiB 제한에 더해 이미지 2천만 픽셀 이하, 단일 프레임으로 제한한다. 정규화 결과도 30 MiB 이하이다. 디코딩 실패/제한 위반은 `400 UPLOAD_FILE_INVALID_TYPE`이다.
- 업로드 실패·트랜잭션 롤백 및 회원탈퇴 파일 정리에 원본과 정규화본을 모두 포함한다.

## OCR 조회 응답

`GET /api/v1/ocr-jobs/{job_id}`의 `data.source_image`:

```json
{"normalized": true, "width": 1200, "height": 1600, "url": "/api/v1/documents/{document_id}/normalized-file"}
```

정규화본 없는 문서는 `normalized: false`, 나머지는 `null`이다. 이 경우 조회 결과의 필드 `source_location`도 `null`로 반환한다. 누락/수동 입력 필드처럼 근거 좌표가 없으면 강조하지 않는다.

## 인증 이미지 조회

`GET /api/v1/documents/{document_id}/normalized-file`은 소유권 확인 후 `image/png` 파일을 반환한다. 인증이 필요하며 `Cache-Control: no-store`가 적용된다. 비소유 문서, 정규화본 없음, 파일 없음은 `404 MEDICAL_DOCUMENT_NOT_FOUND`이다. 기존 `/file`은 원본 조회를 유지한다.

Frontend는 토큰을 포함한 fetch로 이미지를 받아 blob URL로 표시한다. `normalized=true`이고 유효한 크기·좌표가 있으며 이미지 로딩이 성공한 경우에만 overlay를 활성화한다. 좌표는 정규화 이미지 픽셀 기준으로 표시 크기에 비례 변환한다. 이미지 바깥 좌표·다른 페이지·로딩 실패는 강조하지 않는다. 원본 이미지에 정규화 좌표를 겹치지 않는다.

## DB

`medical_document`에 nullable `normalized_object_key`, `normalized_width`, `normalized_height`를 추가한다. 세 값은 모두 NULL이거나 JPEG/PNG의 비어 있지 않은 key와 양수 크기로 함께 존재해야 한다.

## 결정 근거

[#809](https://github.com/AI-HealthCare-05/AH_05_04/issues/809)의 Backend·Worker 검토 승인에 따른다. Pillow는 app 의존성으로만 추가하며 Worker는 저장된 정규화본을 사용한다.
