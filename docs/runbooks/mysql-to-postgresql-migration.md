# MySQL에서 PostgreSQL로 전환 Runbook

## 목적

기존 MySQL 데이터를 PostgreSQL로 일괄 이관하고 정합성을 검증한 뒤 애플리케이션 연결을 전환하는 절차를 정의합니다.

이 작업은 물리적 DB 엔진만 변경합니다. API 계약, 논리적 테이블 구조, 기본키·외래키, UUID 문자열, Enum과 상태 의미는 변경하지 않습니다.

실제 환자정보, 의료문서 원문, 비밀번호와 API Key를 명령 출력·문서·로그에 기록하지 않습니다.

## 관련 파일

- [이관 스크립트](../../scripts/db/migrate_mysql_to_postgresql.py)
- [정합성 검증 스크립트](../../scripts/db/verify_postgresql_migration.py)
- [데이터 구조](../data-schema.md)
- [배포 가이드](../deployment.md)

## 기본 원칙

- 검증 완료 전 MySQL 컨테이너·데이터·volume을 삭제하지 않습니다.
- `docker compose down -v`, `docker volume rm`, `docker system prune --volumes`를 실행하지 않습니다.
- MySQL과 PostgreSQL 사이의 양방향 실시간 동기화는 지원하지 않습니다.
- 이관 중에는 MySQL 원본 데이터에 새로운 쓰기가 발생하지 않도록 애플리케이션 write를 중지합니다.
- PostgreSQL 대상 업무 테이블은 이관 전에 비어 있어야 합니다.
- dry-run과 정합성 검증을 생략하지 않습니다.
- 명령과 로그에는 환경변수 이름과 집계 결과만 기록하고 실제 자격증명은 기록하지 않습니다.

## 1. 사전 확인

다음을 확인합니다.

- 현재 배포 application image 또는 commit
- 현재 Alembic revision
- MySQL 컨테이너와 volume 식별자
- PostgreSQL 컨테이너와 volume 식별자
- MySQL·PostgreSQL 시간대
- MySQL 업무 테이블별 행 수
- 사용할 백업 저장 위치와 접근 권한
- 복구 담당자와 전환 승인자

원본 MySQL과 PostgreSQL 컨테이너가 실행 중인지 확인합니다.

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}'
```

## 2. MySQL 백업

애플리케이션의 MySQL 쓰기를 중지한 후 백업합니다. 백업 파일은 저장소 내부나 Git 추적 경로에 저장하지 않습니다.

`/absolute/path/...` 같은 자리표시자를 그대로 실행하지 않습니다. 실행 환경에 맞는 저장소 외부 절대 경로를 `MIGRATION_BACKUP_DIR`로 먼저 지정합니다.

```bash
# 실행 환경에 맞는 저장소 외부 절대 경로를 지정합니다.
export MIGRATION_BACKUP_DIR="<저장소 외부의 실제 절대 경로>"

# 환경변수가 없으면 백업을 시작하지 않습니다.
migration_backup_dir="${MIGRATION_BACKUP_DIR:?MIGRATION_BACKUP_DIR를 먼저 설정하세요.}"

# 상대 경로 사용을 차단합니다.
case "$migration_backup_dir" in
  /*) ;;
  *)
    echo "오류: MIGRATION_BACKUP_DIR는 절대 경로여야 합니다."
    exit 1
    ;;
esac

# 이후 생성되는 백업과 체크섬 파일은 소유자만 읽고 쓸 수 있게 합니다.
# 백업 파일을 만든 뒤 chmod하는 방식은 생성 직후 노출 구간이 생길 수 있습니다.
umask 077

mkdir -p "$migration_backup_dir"

# 기존에 존재하던 디렉터리에도 소유자 전용 권한을 적용합니다.
chmod 700 "$migration_backup_dir"
```

기존 파일을 덮어쓰지 않도록 실행 시각을 파일명에 포함합니다.

```bash
backup_file="$migration_backup_dir/mysql-before-postgresql-$(date +%Y%m%d-%H%M%S).sql"

test ! -e "$backup_file" || {
  echo "오류: 같은 이름의 백업 파일이 이미 있습니다."
  exit 1
}

docker exec mysql sh -lc \
  'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump \
    --user=root \
    --single-transaction \
    --routines \
    --triggers \
    --set-gtid-purged=OFF \
    --databases "$MYSQL_DATABASE"' \
  > "$backup_file"

test -s "$backup_file" || {
  echo "오류: 백업 파일이 생성되지 않았거나 비어 있습니다."
  exit 1
}
```

비밀번호 값은 명령 인수나 로그에 직접 기록하지 않고 MySQL 컨테이너의 환경변수로만 참조합니다.

백업 파일의 체크섬을 생성하고 즉시 검증합니다.

```bash
checksum_file="${backup_file}.sha256"

shasum -a 256 "$backup_file" > "$checksum_file"
shasum -a 256 -c "$checksum_file"
```

검증 결과가 `OK`인지 확인합니다. 백업 파일과 체크섬 파일은 PostgreSQL 전환, 애플리케이션 검증 및 CI 통과 전까지 삭제하지 않습니다.

## 3. 백업 복원 리허설

백업 파일이 실제로 복원 가능한지 기존 MySQL과 분리된 임시 컨테이너에서 검증합니다.

임시 컨테이너는 외부 네트워크와 영구 volume을 사용하지 않습니다. 기존 `mysql` 컨테이너 및 원본 데이터에는 접근하지 않습니다.

```bash
restore_container="ah0504-mysql-restore-check"

if docker inspect "$restore_container" >/dev/null 2>&1; then
  echo "오류: 동일한 이름의 컨테이너가 이미 있습니다."
  exit 1
fi

docker run -d \
  --name "$restore_container" \
  --network none \
  --tmpfs /var/lib/mysql:rw \
  -e MYSQL_ALLOW_EMPTY_PASSWORD=yes \
  mysql:8.0
```

임시 MySQL이 준비될 때까지 최대 60초 동안 확인합니다.

```bash
restore_ready=false

for attempt in $(seq 1 30); do
  if docker exec "$restore_container" \
    mysqladmin ping --user=root --silent
  then
    restore_ready=true
    break
  fi

  sleep 2
done

if [ "$restore_ready" != "true" ]; then
  echo "오류: 복원 검증용 MySQL이 제한 시간 안에 준비되지 않았습니다."
  exit 1
fi
```

체크섬 검증을 통과한 백업 파일을 임시 MySQL에 복원합니다.

```bash
shasum -a 256 -c "$checksum_file"

docker exec -i "$restore_container" \
  mysql --user=root \
  < "$backup_file"
```

원본 MySQL의 DB 이름을 사용하여 복원된 테이블과 주요 행 수를 확인합니다. 실제 행 내용은 출력하지 않습니다.

```bash
migration_db_name="$(
  docker exec mysql sh -lc 'printf "%s" "$MYSQL_DATABASE"'
)"

test -n "$migration_db_name" || {
  echo "오류: MySQL DB 이름을 확인할 수 없습니다."
  exit 1
}

docker exec "$restore_container" \
  mysql \
  --user=root \
  --database="$migration_db_name" \
  --execute='SHOW TABLES;'

docker exec "$restore_container" \
  mysql \
  --user=root \
  --database="$migration_db_name" \
  --execute='
    SELECT "user" AS table_name, COUNT(*) AS row_count FROM `user`
    UNION ALL
    SELECT "medical_document", COUNT(*) FROM medical_document
    UNION ALL
    SELECT "ocr_job", COUNT(*) FROM ocr_job
    UNION ALL
    SELECT "extracted_field", COUNT(*) FROM extracted_field;
  '
```

원본 MySQL의 사전 집계 결과와 복원된 주요 테이블의 행 수가 같은지 확인합니다.

검증이 끝나면 명시적으로 생성한 임시 컨테이너만 삭제합니다.

```bash
docker rm -f "$restore_container"
```

백업 파일, 체크섬 파일, 기존 `mysql` 컨테이너와 MySQL volume은 삭제하지 않습니다.

## 4. PostgreSQL 스키마 준비

이관 대상 PostgreSQL은 기존 업무 데이터가 없는 새 DB여야 합니다. 기존 데이터가 있는 PostgreSQL에 이관 스크립트를 실행하지 않습니다.

### 로컬·개발환경

PostgreSQL을 시작합니다.

```bash
docker compose \
  --env-file envs/.local.env \
  -f docker-compose.yml \
  up -d postgres
```

PostgreSQL이 정상 상태인지 확인합니다.

```bash
docker compose \
  --env-file envs/.local.env \
  -f docker-compose.yml \
  ps postgres
```

Alembic migration을 적용합니다.

```bash
docker compose \
  --env-file envs/.local.env \
  -f docker-compose.yml \
  run --rm migrate
```

### 배포환경

배포환경에서는 검증된 고정 이미지와 `envs/.prod.env`를 사용합니다. 로컬용 Compose 파일과 개발용 이미지를 사용하지 않습니다.

```bash
docker compose \
  --env-file envs/.prod.env \
  -f infra/docker/docker-compose.prod.yml \
  up -d postgres
```

```bash
docker compose \
  --env-file envs/.prod.env \
  -f infra/docker/docker-compose.prod.yml \
  run --rm migrate
```

### Alembic 적용 결과 확인

`alembic_version`이 생성되고 revision이 한 행 존재하는지 확인합니다.

```bash
docker compose \
  --env-file envs/.local.env \
  -f docker-compose.yml \
  exec postgres \
  sh -lc 'psql \
    -v ON_ERROR_STOP=1 \
    -U "$POSTGRES_USER" \
    -d "$POSTGRES_DB" \
    -c "SELECT version_num FROM alembic_version;"'
```

생성된 테이블 목록을 확인합니다.

```bash
docker compose \
  --env-file envs/.local.env \
  -f docker-compose.yml \
  exec postgres \
  sh -lc 'psql \
    -v ON_ERROR_STOP=1 \
    -U "$POSTGRES_USER" \
    -d "$POSTGRES_DB" \
    -c "\dt"'
```

다음 조건을 모두 확인합니다.

- `alembic_version` 테이블이 존재합니다.
- Alembic revision이 한 행 존재합니다.
- 애플리케이션 업무 테이블이 모두 존재합니다.
- 데이터 이관 전 업무 테이블의 행 수가 0입니다.
- 스키마 생성 오류가 발생하지 않았습니다.

배포환경에서 확인할 때는 위 명령의 환경 파일과 Compose 파일을 각각 `envs/.prod.env`, `infra/docker/docker-compose.prod.yml`로 바꿉니다.

## 5. 이관 환경변수 및 dry-run

이관 스크립트는 애플리케이션의 비동기 DB URL을 사용하지 않고 MySQL과 PostgreSQL에 각각 동기 드라이버로 접속합니다.

필요한 환경변수는 다음과 같습니다.

| 구분 | 환경변수 | 필수 여부 | 기본값 |
|---|---|---:|---|
| MySQL | `MYSQL_SOURCE_USER` | 필수 | 없음 |
| MySQL | `MYSQL_SOURCE_PASSWORD` | 필수 | 없음 |
| MySQL | `MYSQL_SOURCE_DB` | 필수 | 없음 |
| MySQL | `MYSQL_SOURCE_HOST` | 선택 | `127.0.0.1` |
| MySQL | `MYSQL_SOURCE_PORT` | 선택 | `3306` |
| PostgreSQL | `DB_USER` | 필수 | 없음 |
| PostgreSQL | `DB_PASSWORD` | 필수 | 없음 |
| PostgreSQL | `DB_NAME` | 필수 | 없음 |
| PostgreSQL | `POSTGRES_MIGRATION_HOST` | 선택 | `127.0.0.1` |
| PostgreSQL | `DB_EXPOSE_PORT` | 선택 | `5432` |
| 실행 제어 | `MIGRATION_DRY_RUN` | 선택 | `true` |
| 실행 제어 | `ALLOW_EMPTY_SOURCE` | 선택 | `false` |

비밀번호 값을 명령문, 문서 또는 로그에 직접 입력하지 않습니다. 로컬에서는 기존 컨테이너의 환경변수와 Git에서 제외된 `envs/.local.env`를 사용합니다.

```bash
# Git에서 제외된 로컬 PostgreSQL 설정을 현재 shell로 불러옵니다.
set -a
. envs/.local.env
set +a

# MySQL 자격증명은 실행 중인 원본 컨테이너에서 가져오며 출력하지 않습니다.
export MYSQL_SOURCE_USER="$(
  docker exec mysql sh -lc 'printf "%s" "$MYSQL_USER"'
)"
export MYSQL_SOURCE_PASSWORD="$(
  docker exec mysql sh -lc 'printf "%s" "$MYSQL_PASSWORD"'
)"
export MYSQL_SOURCE_DB="$(
  docker exec mysql sh -lc 'printf "%s" "$MYSQL_DATABASE"'
)"

export MYSQL_SOURCE_HOST="127.0.0.1"
export MYSQL_SOURCE_PORT="3306"
export POSTGRES_MIGRATION_HOST="127.0.0.1"

# 안전 기본값입니다. 원본이 비어 있으면 중단하고 실제 복사는 수행하지 않습니다.
export ALLOW_EMPTY_SOURCE="false"
export MIGRATION_DRY_RUN="true"
```

필수 환경변수가 설정됐는지만 확인합니다. 값 자체는 출력하지 않습니다.

```bash
for variable_name in \
  MYSQL_SOURCE_USER \
  MYSQL_SOURCE_PASSWORD \
  MYSQL_SOURCE_DB \
  DB_USER \
  DB_PASSWORD \
  DB_NAME
do
  if [ -z "$(printenv "$variable_name")" ]; then
    echo "오류: $variable_name 환경변수가 설정되지 않았습니다."
    exit 1
  fi
done

echo "이관 필수 환경변수 확인 완료"
```

배포환경에서는 컨테이너 환경변수를 복사하지 않고 승인된 secret 관리 수단으로 같은 변수들을 주입합니다.

### Dry-run 실행

Dry-run은 원본과 대상 DB 연결, 테이블 존재 여부, 원본 행 수, PostgreSQL 대상 테이블의 빈 상태를 확인합니다. 데이터를 복사하지 않습니다.

```bash
MIGRATION_DRY_RUN=true \
uv run --group db-migration \
python scripts/db/migrate_mysql_to_postgresql.py
```

다음을 확인합니다.

- 모든 대상 테이블의 MySQL·PostgreSQL 행 수가 출력됩니다.
- PostgreSQL 업무 테이블의 행 수가 모두 0입니다.
- `사전검증만 완료했습니다`가 출력됩니다.
- 데이터 복사 완료 메시지는 출력되지 않습니다.
- 연결 정보와 비밀번호는 출력되지 않습니다.
- MySQL 이메일을 소문자로 변환했을 때 충돌하는 계정 그룹이 없습니다.

`MIGRATION_DRY_RUN`을 생략해도 안전 기본값 `true`가 적용되어 실제 이관이 수행되지 않습니다.

## 6. 실제 이관 및 정합성 검증

실제 이관은 다음 조건을 모두 충족한 경우에만 실행합니다.

- MySQL 애플리케이션 쓰기가 중지되어 있습니다.
- MySQL 백업과 체크섬 검증이 완료되었습니다.
- 격리된 MySQL에서 백업 복원 리허설을 통과했습니다.
- PostgreSQL에 Alembic 스키마가 적용되었습니다.
- PostgreSQL 업무 테이블이 모두 비어 있습니다.
- 이관 스크립트의 dry-run을 통과했습니다.
- MySQL과 PostgreSQL의 연결 대상 및 시간대 정책을 확인했습니다.

### 실제 이관

실제 복사를 허용하려면 `MIGRATION_DRY_RUN=false`를 명시해야 합니다.

```bash
MIGRATION_DRY_RUN=false \
uv run --group db-migration \
python scripts/db/migrate_mysql_to_postgresql.py
```

스크립트는 외래키 의존 순서에 따라 테이블을 복사하며 전체 작업을 PostgreSQL의 단일 트랜잭션으로 처리합니다.

다음을 확인합니다.

- 기존 `user.email`은 신규 회원가입과 동일한 소문자 저장 규칙으로 이관됩니다.
- 각 테이블에 `[이관완료]`와 복사 행 수가 출력됩니다.
- 각 테이블의 복사 행 수가 사전검증의 MySQL 행 수와 같습니다.
- 마지막에 `MySQL에서 PostgreSQL로 데이터 이관을 완료했습니다.`가 출력됩니다.
- 비밀번호, 연결 URL, 실제 의료문서 내용은 출력되지 않습니다.

중간 테이블에서 오류가 발생하면 트랜잭션이 롤백됩니다. 실패 후에는 PostgreSQL 대상 테이블이 비어 있는지 다시 확인하고 dry-run부터 재수행합니다.

대상 테이블에 일부 데이터가 남아 있다면 자동으로 삭제하거나 스크립트를 강제로 재실행하지 않습니다. 원인을 확인하고 전환 담당자의 승인을 받은 후 복구 절차를 결정합니다.

정상 이관이 완료된 PostgreSQL에는 같은 스크립트를 다시 실행하지 않습니다. 대상 테이블이 비어 있지 않으면 스크립트가 중복 이관을 차단합니다.

### 정합성 검증

이관 직후 MySQL 쓰기를 재개하거나 PostgreSQL로 애플리케이션을 전환하기 전에 검증 스크립트를 실행합니다.
`user.email`은 의도된 변환 컬럼입니다. 검증기는 MySQL 원본 이메일을 소문자로 정규화한 기대값과 PostgreSQL 저장값을 비교합니다. 실제 이메일이나 사용자 식별자는 검증 로그에 출력하지 않습니다.
```bash
uv run --group db-migration \
python scripts/db/verify_postgresql_migration.py
```

검증 스크립트가 확인하는 항목은 다음과 같습니다.

- 전체 업무 테이블의 MySQL·PostgreSQL 행 수
- 기본키 집합
- 외래키 참조
- 필수 필드의 `NULL` 분포
- Enum 및 상태값 분포
- 날짜·시간 값의 실제 시각
- UTC 및 Asia/Seoul 시간대 변환 결과

모든 검증을 통과하면 다음 메시지가 출력됩니다.

```text
MySQL → PostgreSQL 이관 정합성 검증을 통과했습니다.
```

하나라도 불일치하면 PostgreSQL 전환을 중단합니다. 불일치를 무시하거나 검증 스크립트를 우회하지 않습니다.

## 7. PostgreSQL 애플리케이션 검증 및 전환

정합성 검증을 통과한 뒤 FastAPI를 PostgreSQL에 연결합니다.

로컬 환경에서는 다음과 같이 실행합니다.

```bash
docker compose \
  --env-file envs/.local.env \
  -f docker-compose.yml \
  up -d fastapi
```

서비스 상태를 확인합니다.

```bash
docker compose \
  --env-file envs/.local.env \
  -f docker-compose.yml \
  ps
```

OpenAPI 응답을 확인합니다.

```bash
curl \
  --silent \
  --show-error \
  --output /dev/null \
  --write-out 'HTTP %{http_code}\n' \
  http://localhost:8000/api/openapi.json
```

정상 응답은 `HTTP 200`입니다.

### 핵심 API smoke test

실제 환자정보나 의료문서를 사용하지 않고 합성 테스트 데이터만 사용합니다.

다음을 확인합니다.

- 인증 없이 보호 API를 호출하면 `401`이 반환됩니다.
- 합성 사용자 회원가입이 `201`로 완료됩니다.
- 합성 사용자 로그인이 `200`으로 완료됩니다.
- 발급된 토큰으로 `/api/v1/users/me`가 `200`을 반환합니다.
- 합성 문서 업로드와 OCR 실행이 완료됩니다.
- OCR 결과 조회·검수 흐름이 정상 동작합니다.
- 처방 확정 API가 계약에 맞게 동작합니다.
- 복약 가이드 및 챗봇의 현재 MVP 동기 흐름이 정상 동작합니다.
- 오류 응답에 비밀번호, DB URL, 스택 트레이스, 문서 원문이 노출되지 않습니다.

Swagger UI는 다음 주소에서 사용할 수 있습니다.

```text
http://localhost:8000/api/docs
```

테스트 후 합성 데이터만 외래키 의존 순서에 따라 삭제합니다. 원본 이관 데이터는 삭제하지 않습니다.

### 자동화 검사

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy app ai_worker
git diff --check
```

PostgreSQL 서비스가 연결된 CI도 통과해야 합니다. 로컬 검사만으로 배포 전환을 승인하지 않습니다.

### 배포 전환

다음 순서를 변경하거나 생략하지 않습니다.

1. 외부 요청을 유지보수 모드 또는 승인된 게이트에서 차단합니다.
2. FastAPI와 AI Worker의 신규 DB 쓰기를 중지합니다.
3. 진행 중인 요청이 종료되어 MySQL 쓰기가 더 이상 발생하지 않는지 확인합니다.
4. 자체 운영 MySQL이라면 `read_only`와 `super_read_only`를 활성화합니다. 관리형 DB라면 제공자의 읽기 전용 전환 기능을 사용합니다.
5. MySQL 백업과 격리 복원 리허설을 완료합니다.
6. PostgreSQL에 Alembic schema를 적용합니다.
7. dry-run 후 실제 데이터 이관을 실행합니다.
8. PK·FK·전체 컬럼·상태·시간 정합성 검증을 통과시킵니다.
9. 외부 요청이 차단된 상태에서 FastAPI와 Worker를 PostgreSQL로 실행합니다.
10. 합성 데이터만 사용해 핵심 API smoke test와 모니터링을 확인합니다.
11. 전환 승인자가 검증 결과를 확인한 뒤에만 외부 요청을 재개합니다.
12. 안정화가 확인될 때까지 기존 MySQL과 백업을 삭제하지 않습니다.

자체 운영 MySQL에서는 애플리케이션 쓰기를 중지한 뒤 승인된 운영 담당자가 다음과 같이 읽기 전용 상태로 전환합니다.

```sql
-- 신규 쓰기를 차단합니다.
SET GLOBAL read_only = ON;
SET GLOBAL super_read_only = ON;

-- 두 설정이 모두 활성화됐는지 확인합니다.
SELECT @@GLOBAL.read_only, @@GLOBAL.super_read_only;
```
관리형 MySQL에서는 위 SQL을 임의로 실행하지 않고 제공자가 지원하는 읽기 전용 전환 기능과 승인 절차를 사용합니다.

배포환경에서는 다음 항목을 먼저 기록합니다.

- 전환 대상 commit
- PostgreSQL 대응 고정 application image
- Alembic revision
- 배포 승인자
- 전환 시작 시각
- 백업 파일 식별자와 체크섬
- 정합성 검증 결과
- smoke test 결과

`envs/.prod.env`의 실제 운영 설정은 승인된 secret 관리 수단으로 제공합니다.

다음 설정이 PostgreSQL을 가리키는지 확인합니다.

```text
DB_HOST=postgres
DB_PORT=5432
DB_USER=<운영 PostgreSQL 사용자>
DB_PASSWORD=<운영 secret 관리 수단에서 주입>
DB_NAME=<운영 PostgreSQL DB>
SQLALCHEMY_ECHO=false
```

배포환경의 전체 설정값이나 비밀번호를 화면 또는 로그로 출력하지 않습니다.

승인된 PostgreSQL 대응 이미지로 migration과 애플리케이션을 실행합니다.

```bash
docker compose \
  --env-file envs/.prod.env \
  -f infra/docker/docker-compose.prod.yml \
  up -d
```

`migrate` 서비스가 성공적으로 완료된 후에만 FastAPI가 시작되어야 합니다. smoke test와 모니터링 확인 전에는 사용자 쓰기 트래픽을 허용하지 않습니다.

## 8. 전환 중단 및 재시도 기준

다음 중 하나라도 발생하면 PostgreSQL 전환을 중단합니다.

- MySQL 쓰기를 중지할 수 없습니다.
- 백업 파일 생성 또는 체크섬 검증에 실패합니다.
- 격리 복원 리허설에 실패합니다.
- 원본 MySQL의 행 수가 예상과 다릅니다.
- PostgreSQL 대상 업무 테이블이 비어 있지 않습니다.
- Alembic migration이 실패합니다.
- dry-run이 실패합니다.
- 실제 이관 중 오류가 발생합니다.
- 행 수, PK, FK, 상태값, `NULL`, 시간 검증이 일치하지 않습니다.
- FastAPI가 PostgreSQL에 연결되지 않습니다.
- 핵심 API smoke test가 실패합니다.
- 전체 테스트 또는 PostgreSQL 기반 CI가 실패합니다.
- API 응답이나 로그에서 비밀번호, 연결 URL 또는 민감정보가 노출됩니다.
- 전환 중 원본 MySQL에 새로운 쓰기가 확인됩니다.

실패 원인을 수정한 뒤 다음 조건을 다시 확인합니다.

1. 원본 MySQL 상태가 전환 시작 시점과 일치합니다.
2. 유효한 백업과 체크섬이 존재합니다.
3. PostgreSQL 대상 업무 테이블이 비어 있습니다.
4. Alembic 스키마가 올바릅니다.
5. dry-run을 다시 통과합니다.

부분 이관 데이터를 임의로 수정하거나 삭제한 뒤 바로 재실행하지 않습니다. 대상 DB 초기화가 필요하면 대상과 범위를 확인하고 별도 승인을 받습니다.

## 9. 롤백 기준과 절차

PostgreSQL 전환은 최종 목표이지만, 데이터 손상이나 서비스 장애 상태로 계속 운영하지 않도록 안전한 복구 기준을 유지합니다.

다음 상황에서는 롤백을 검토합니다.

- 정합성 불일치를 전환 시간 안에 해결할 수 없습니다.
- 핵심 API가 PostgreSQL에서 정상 동작하지 않습니다.
- 데이터 저장 또는 조회 오류가 반복됩니다.
- 배포 후 의료 안전에 영향을 줄 수 있는 데이터 누락이나 변환 오류가 발견됩니다.
- PostgreSQL 대응 애플리케이션이 정상적으로 기동하지 않습니다.

### 롤백 제한

현재 이관은 MySQL에서 PostgreSQL로의 일회성 복사이며 양방향 동기화를 제공하지 않습니다.

사용자 쓰기 트래픽이 PostgreSQL에 들어간 뒤에는 PostgreSQL의 신규 데이터를 MySQL로 자동 반영할 수 없습니다. 따라서 다음 중 하나를 충족하지 않으면 즉시 MySQL로 되돌리지 않습니다.

- PostgreSQL 전환 후 실제 사용자 쓰기가 발생하지 않았습니다.
- PostgreSQL에서 발생한 변경을 식별하고 승인된 방법으로 MySQL에 반영했습니다.
- 신규 변경을 폐기할 수 있다는 Product·데이터 책임자의 승인을 받았습니다.

### 롤백 절차

1. 사용자 쓰기 트래픽을 즉시 중지합니다.
2. 장애 시각, 증상, 영향 범위를 기록합니다.
3. PostgreSQL에 전환 후 신규 쓰기가 있는지 확인합니다.
4. 보존 중인 MySQL 컨테이너와 데이터 상태를 확인합니다.
5. MySQL 대응 전환 전 application image와 배포 설정을 복원합니다.
6. 애플리케이션 DB 연결을 보존된 MySQL로 되돌립니다.
7. Alembic revision 및 핵심 테이블 행 수를 확인합니다.
8. 합성 데이터로 인증, 문서, OCR 및 핵심 API smoke test를 수행합니다.
9. 검증과 승인 후에만 사용자 트래픽을 재개합니다.

현재 PostgreSQL 전용 코드를 단순히 MySQL 호스트에 연결해서는 안 됩니다. 롤백에는 반드시 MySQL 드라이버와 타입 처리를 포함한 전환 전 application image 및 배포 구성이 필요합니다.

롤백 완료 후 PostgreSQL 데이터는 삭제하지 않고 원인 분석 및 누락 데이터 확인을 위해 격리 보존합니다.

## 10. 완료 증적과 보존 정책

전환 완료 시 다음 증적을 남깁니다. 실제 자격증명과 데이터 원문은 기록하지 않습니다.

- 전환 commit 및 고정 이미지 식별자
- Alembic revision
- 백업 파일 식별자
- SHA-256 체크섬 검증 결과
- 격리 복원 리허설 결과
- 테이블별 이관 행 수
- PK·FK·상태·시간 정합성 검증 결과
- 핵심 API smoke test 결과
- 전체 테스트, Ruff, format, Mypy 결과
- PostgreSQL 기반 CI 결과
- 전환·검증·승인 시각
- 전환 담당자와 승인자
- 발견된 문제 및 후속 Issue

백업 파일에는 민감정보가 포함될 수 있으므로 접근 권한을 제한하고 승인된 보관 위치에서 암호화하여 관리합니다.

로컬 백업 파일 권한은 다음과 같이 제한할 수 있습니다.

```bash
# umask 077이 적용된 상태에서 생성했는지 최종 확인합니다.
ls -ld "$migration_backup_dir"
ls -l "$backup_file" "$checksum_file"
```
백업 디렉터리는 소유자만 접근 가능한 `700`, 백업 파일과 체크섬은 소유자만 읽고 쓸 수 있는 `600` 수준이어야 합니다. 실제 백업 내용은 화면에 출력하지 않습니다.

작업이 끝나면 현재 shell에서 이관용 자격증명을 제거합니다.

```bash
unset MYSQL_SOURCE_USER
unset MYSQL_SOURCE_PASSWORD
unset MYSQL_SOURCE_DB
unset MYSQL_SOURCE_HOST
unset MYSQL_SOURCE_PORT
unset POSTGRES_MIGRATION_HOST
unset MIGRATION_DRY_RUN
unset ALLOW_EMPTY_SOURCE
```

다음 조건을 모두 충족하기 전에는 MySQL 컨테이너, volume, 백업 파일을 삭제하지 않습니다.

- PostgreSQL 정합성 검증 통과
- 핵심 API smoke test 통과
- 전체 자동화 테스트 통과
- PostgreSQL 기반 CI 통과
- 합의된 안정화 관찰 기간 종료
- 데이터 책임자와 배포 책임자의 삭제 승인

삭제 승인을 받기 전에는 `docker compose down -v`, `docker volume rm`, `docker system prune --volumes`를 실행하지 않습니다.

MySQL 데이터와 백업의 최종 보존기간 및 폐기 방법은 프로젝트의 개인정보·보안 정책에 따라 별도로 확정합니다.
