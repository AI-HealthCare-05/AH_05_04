# #809 OCR 이미지 좌표계 통일

상태: #809 Backend/Worker 검토 승인 범위를 구현. 운영 배포 및 Frontend 연결은 미완료.

## 결정

원본 다운로드는 유지하며, 신규 JPEG/PNG 업로드에 EXIF 방향 보정 및 메타데이터 제거한 PNG를 추가 생성한다. OCR 입력과 Frontend 강조 좌표계는 이 정규화본을 공유한다. OCR 조회에 source_image를 추가하고, 인증·소유권 검사를 거치는 normalized-file 경로로 이미지를 제공한다. 레거시/PDF에는 정규화본과 강조를 제공하지 않는다.

원본과 정규화본의 실패/탈퇴 정리를 동일하게 적용하며 Pillow 의존성은 app에 둔다. DB migration은 809a2b3c4d5e이다.

## 근거 및 계약

- [Backend 승인](https://github.com/AI-HealthCare-05/AH_05_04/issues/809#issuecomment-5726380764)
- [Worker 승인 및 파일 수명주기 조건](https://github.com/AI-HealthCare-05/AH_05_04/issues/809#issuecomment-5726390659)
- [상세 API/DB 및 Frontend 계약](../contracts/current/ocr-normalized-image.md)

## 검증

EXIF 8방향, 원본 보존, 잘못된 이미지/다중 프레임/크기 제한, 트랜잭션 롤백, 인증 이미지 소유권, Worker 입력 선택, 회원탈퇴 시 두 파일 삭제 및 기존 OCR 회귀를 검증했다. 빈 PostgreSQL 테스트 DB에서 전체 migration upgrade가 통과했다.
