# #267 대용량 처방전 업로드 메모리 검토

## 구현 변경

- Backend는 파일을 청크 단위로 최대 30MiB + 1바이트까지 읽는다.
- 파일 크기 제한은 30MiB를 유지한다.
- 제한 초과 시 DB 생성·저장 전에 UPLOAD_FILE_TOO_LARGE로 거부한다.
- OCR 전송은 read_bytes() 사전 적재를 제거하고 파일 객체를 전달한다.
- OCR 요청 성공·timeout·취소 예외 발생 후 파일 닫힘을 검증했다.
- Nginx API 요청 본문 제한은 multipart 부가 데이터를 고려해 32MiB로 설정했다.
- 전체 요청이 32MiB를 초과하면 Nginx가 413으로 거부한다.

## 로컬 함수 메모리 측정

- 측정일: 2026-09-07
- 실행 환경: macOS 로컬 uv Python 환경
- 측정 도구: tracemalloc
- 대상: MedicalDocumentService._read_upload_content
- 입력: 요청별 30MiB 합성 파일
- 방법: 동시 읽기 후 모든 결과 버퍼를 보유한 상태에서 측정

| 동시 읽기 수 | 결과 보유 시 할당량 | 최대 할당량 |
| ---: | ---: | ---: |
| 1 | 30.42MiB | 64.17MiB |
| 3 | 90.02MiB | 128.78MiB |
| 6 | 180.04MiB | 219.80MiB |

실행 명령:

    PYTHONPATH="$PWD/backend:$PWD" uv run python scripts/measure_upload_memory.py

## 측정 한계

- Python에서 추적한 메모리 할당량이며 프로세스 전체 RSS가 아니다.
- HTTP multipart 파싱, DB 처리, 파일 저장, OCR 실행은 포함하지 않는다.
- 운영 환경의 메모리 사용량이나 처리량을 증명하지 않는다.
- 별도로 관찰한 로컬 FastAPI 컨테이너 사용량 131.7MiB와 합산하지 않는다.

## 동시성 검토

- 배포 Dockerfile은 Backend 프로세스 3개를 실행한다.
- 프로세스 수는 동시 업로드 요청 수 제한이 아니다.
- 현재 확인한 FastAPI 컨테이너에는 개별 메모리 제한이 없다.
- Worker Job 동시성 기본값은 프로세스당 1이며 업로드 동시성과 별개다.
- 동시 업로드 수가 늘면 결과 버퍼 메모리도 증가한다.
- 운영 허용 동시성은 아직 미확정이다.
- Backend 메모리 예산, 실제 프로세스 수, multipart 임시 저장 공간,
  실제 HTTP 부하 측정 결과를 근거로 허용 동시성과 제한 방식을 결정한다.

## 검증 현황

- 업로드 관련 테스트: 17 passed
- OCR Engine·Worker Adapter 테스트: 36 passed
- OCR 구현·테스트 파일 Mypy: 통과
- prod_http.conf 임시 컨테이너 nginx -t: 통과
- 업로드 API 테스트: 5 passed
  - 정확히 30MiB 업로드: 201, 파일 저장 크기 확인
  - 30MiB + 1바이트 업로드: 400 / UPLOAD_FILE_TOO_LARGE, 파일 미저장
  - ASGITransport 기반 API 검증이며 Nginx·실제 OCR 호출은 포함하지 않음
- 메모리 측정 스크립트 Mypy: 통과
- 전체 Ruff 검사: 통과
- 전체 서식 검사: 430 files already formatted
- Mypy: 373개 소스 파일 통과
- Redis 실행 후 전체 테스트 스크립트: 통과
- git diff --check: 통과

## 남은 완료 조건

- 운영 Backend 메모리 예산과 허용 동시성 확정
- 실제 배포 Nginx 설정 적용 및 HTTPS 설정 검증
- 30MiB 부근 합성 이미지의 업로드 → OCR 전체 사이클 검증
- 실제 HTTP 경로의 경계·초과 업로드 응답 확인
- OCR 전송의 동기 파일 읽기가 Worker heartbeat에 미치는 영향 확인
