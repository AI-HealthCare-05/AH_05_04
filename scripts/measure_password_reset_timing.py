"""#206 PR #404 리뷰(권가빈): `/password-reset/request`가 계정 존재 여부에 따라
처리시간이 달라지는 타이밍 사이드채널을 완화하기 위해, 목표 응답시간(padding
target)을 정하기 전 실제 처리시간 분포를 측정하는 1회성 벤치마크.

pytest가 아니라 별도 스크립트인 이유: CI 환경의 타이밍 변동성 때문에 신뢰할 수
있는 assert 기준을 세우기 어렵다 — 로컬에서 직접 실행해 p50/p95/p99를 확인하고,
그 결과를 계약 문서·PR에 근거로 남긴다.

각 케이스는 앞으로 추가할 padding 로직과 동일한 모양(요청마다 새 세션을 열어 읽기·
조건부 쓰기를 수행하고 즉시 commit)으로 측정한다 — 현재 프로덕션 코드는 실제
commit을 FastAPI dependency teardown(응답 전송 후)으로 미루지만, padding을
넣으려면 그 전에 명시적으로 commit해 커넥션을 반납해야 하므로 그 모양을 미리
재현한다."""

import asyncio
import statistics
import time
from uuid import uuid4

from app.core.db.databases import AsyncSessionFactory, close_database
from app.models.users import User
from app.repositories.password_reset_repository import PasswordResetRepository
from app.repositories.refresh_session_repository import RefreshSessionRepository
from app.repositories.user_repository import UserRepository
from app.services.auth import AuthService

SAMPLE_SIZE = 200
CONCURRENCY_LEVELS = (1, 20)
# bcrypt 형식 자리표시자 — 이 벤치마크는 로그인 경로를 타지 않으므로 실제 해시가 아니어도 된다.
_PLACEHOLDER_HASH = "$2b$12$" + "a" * 53


async def _create_users(count: int) -> list[str]:
    emails: list[str] = []
    async with AsyncSessionFactory() as session:
        for _ in range(count):
            email = f"timing-bench-{uuid4().hex[:12]}@example.com"
            session.add(User(email=email, hashed_password=_PLACEHOLDER_HASH, name="타이밍벤치테스터"))
            emails.append(email)
        await session.commit()
    return emails


async def _time_request(email: str) -> float:
    start = time.perf_counter()
    async with AsyncSessionFactory() as session:
        auth_service = AuthService(
            UserRepository(session),
            PasswordResetRepository(session),
            RefreshSessionRepository(session),
        )
        await auth_service.request_password_reset(email)
        await session.commit()
    return time.perf_counter() - start


async def _measure(label: str, emails: list[str], *, concurrency: int) -> None:
    durations: list[float] = []
    for batch_start in range(0, len(emails), concurrency):
        batch = emails[batch_start : batch_start + concurrency]
        durations.extend(await asyncio.gather(*(_time_request(email) for email in batch)))

    durations.sort()
    p50 = statistics.median(durations)
    p95 = durations[int(len(durations) * 0.95) - 1]
    p99 = durations[int(len(durations) * 0.99) - 1]
    print(
        f"{label:24s} concurrency={concurrency:3d} "
        f"p50={p50 * 1000:6.1f}ms p95={p95 * 1000:6.1f}ms p99={p99 * 1000:6.1f}ms "
        f"max={max(durations) * 1000:6.1f}ms n={len(durations)}"
    )


async def main() -> None:
    for concurrency in CONCURRENCY_LEVELS:
        fresh_emails = await _create_users(SAMPLE_SIZE)
        nonexistent_emails = [f"timing-bench-missing-{uuid4().hex[:12]}@example.com" for _ in range(SAMPLE_SIZE)]
        cooldown_emails = await _create_users(SAMPLE_SIZE)
        for email in cooldown_emails:
            await _time_request(email)  # 쿨다운을 시작시키는 워밍업 호출 — 측정 대상 아님.

        print(f"--- concurrency={concurrency} ---")
        await _measure("존재 계정(신규 토큰)", fresh_emails, concurrency=concurrency)
        await _measure("존재하지 않는 계정", nonexistent_emails, concurrency=concurrency)
        await _measure("존재 계정(쿨다운 중)", cooldown_emails, concurrency=concurrency)

    await close_database()


if __name__ == "__main__":
    asyncio.run(main())
