# #267 대용량 처방전 업로드 메모리 검토

## 구현 변경

- Backend는 파일을 청크 단위로 최대 30MiB + 1바이트까지 읽는다.
- 파일 크기 제한은 30MiB를 유지한다.
- 제한 초과 시 DB 생성·저장 전에 UPLOAD_FILE_TOO_LARGE로 거부한다.
- OCR 전송은 read_bytes() 사전 적재를 제거하고 파일 객체를 전달한다.
- OCR 요청 성공·timeout·취소 예외 발생 후 파일 닫힘을 검증했다.
- Nginx API 요청 본문 제한은 multipart 부가 데이터를 고려해 32MiB로 설정했다.
- 전체 요청이 32MiB를 초과하면 Nginx가 본문을 전달하지 않고 내부 오류 경로로 연결해
  `400 / UPLOAD_FILE_TOO_LARGE` 공통 JSON, trace·no-store·CORS 정책을 유지한다.
- 운영 Compose는 FastAPI host 포트를 공개하지 않아 외부 클라이언트의 Nginx 우회를 막는다.
  개발용 Compose의 포트 공개는 유지한다.

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
- 프록시 오류 응답 Backend·업로드 API 테스트: 9 passed
- 실제 Nginx HTTP 통합 검증: 5개 통과
  - Origin 없음·허용·미허용 요청의 크기 초과 응답 확인
  - 400 / UPLOAD_FILE_TOO_LARGE 공통 JSON 확인
  - 본문 trace_id와 X-Trace-Id 일치 및 no-store 확인
  - 내부 오류 경로 직접 접근: 404
  - 일반 요청의 Backend 전달 정상
- 검증 스크립트 Ruff·Mypy: 통과
- 범위: prod_http.conf 기반 Content-Length 초과 처리.
  HTTPS·chunked 업로드·실제 OCR 검증은 포함하지 않음.

## 남은 완료 조건

- 운영 Backend 메모리 예산 확정
- 실제 HTTP 업로드의 RSS·임시 저장 공간·응답시간 부하 측정
- 허용 동시 업로드 수를 숫자로 명시하고 프로세스당 제한인지 전체 제한인지 구분
- 제한 방식과 초과 요청 처리 정책 확정 및 필요한 구현 반영
- 허용 동시성과 한도 초과 상황에서 확정한 정책대로 동작하는지 검증
- 실제 배포 Nginx 설정 적용 및 HTTPS 설정 검증
- 30MiB 부근 합성 이미지의 업로드 → OCR 전체 사이클 검증
- 실제 HTTP 경로의 경계·초과 업로드 응답 확인
- OCR 전송의 동기 파일 읽기가 Worker heartbeat에 미치는 영향 확인

후속 이슈 생성과 담당 조율은 PM에게 요청한 상태다. Backend 제한 정책은 Backend 담당자가
검토하고, Worker/OCR 담당자는 기존 측정 결과 인계와 OCR·heartbeat 확인을 지원한다.
후속 이슈가 생성되면 링크를 연결하고, 위 완료 조건을 충족하기 전까지 #267은 Open으로 유지한다.

## 운영 포트 비공개 검증

- 회귀 테스트: `uv run pytest tests/contract/test_prod_fastapi_network_boundary.py -q`
- 운영 네트워크 경계·프록시 오류·업로드 API 테스트: 11 passed
- 신규 테스트가 변경 전 `8000:8000` 공개 설정을 거부하는 것 확인
- 추가 리뷰 반영 후 전체 Ruff 통과, 서식 검사 439개 파일 통과, Mypy 381개 소스 파일 통과
- 전체 테스트 재실행: Migration 39 passed, Backend·계약·통합 1026 passed / 2 skipped,
  Worker 732 passed, Coverage 95%
- 최초 실행은 Docker 자격 증명 도우미 문제로 이미지 빌드 fixture 4개가 오류였다.
  사용자 로그인 설정을 변경하지 않는 임시 Docker 설정으로 재실행해 전체 통과했다.
- FastAPI의 host port 공개와 host/container 네트워크 우회를 금지하고, Nginx와 공유하는
  bridge 네트워크 및 Nginx의 80·443 진입점을 확인한다.
- 이 테스트는 저장소 Compose 설정 검증이다. 실제 배포 적용 증빙을 대신하지 않는다.
- 선배포 시 PM은 최종 Compose 설정과 재생성된 FastAPI 컨테이너의 port binding이 비어 있는지,
  외부에서 host:8000 연결이 차단되는지, Nginx 일반 API와 초과 업로드 처리가 유지되는지 확인한다.
- 기존 컨테이너에는 파일 수정만으로 적용되지 않으므로 FastAPI 재생성 후 확인한다.
