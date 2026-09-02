"""Worker Stream Adapter의 생성과 의존성 주입 경계입니다."""

from redis.asyncio import Redis

from ai_worker.adapters.redis_stream import RedisStreamAdapter
from ai_worker.core.config import Config


def create_redis_client(config: Config) -> Redis:
    """Worker 설정으로 비동기 Redis Client를 생성합니다."""

    return Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        password=config.REDIS_PASSWORD,
        decode_responses=False,
        socket_connect_timeout=config.REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS,
        socket_timeout=config.REDIS_SOCKET_TIMEOUT_SECONDS,
    )


def create_stream_adapter(
    config: Config,
    *,
    client: Redis | None = None,
) -> RedisStreamAdapter:
    """주입된 Client 또는 설정 기반 Client로 Adapter를 생성합니다."""

    if client is None:
        client = create_redis_client(config)

    return RedisStreamAdapter(
        client,
        stream_name=config.REDIS_STREAM_NAME,
        group_name=config.REDIS_CONSUMER_GROUP,
    )
