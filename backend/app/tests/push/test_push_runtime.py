from unittest.mock import AsyncMock, Mock

from app.apis.v1 import push_routers
from app.commands import process_push
from app.core import config


async def test_production_blocks_by_default_even_when_push_enabled(push_settings, monkeypatch):
    monkeypatch.setattr(config, "ENV", "production")
    monkeypatch.setattr(push_routers, "get_push_settings", lambda: push_settings)
    factory = Mock()
    assert not push_settings.production_enabled
    assert not push_routers.push_settings().enabled
    assert await process_push.process_push_once(settings=push_settings, session_factory=factory) == 0
    factory.begin.assert_not_called()


async def test_production_allows_registration_and_send_when_production_enabled(push_settings, monkeypatch):
    push_settings.production_enabled = True
    monkeypatch.setattr(config, "ENV", "production")
    monkeypatch.setattr(push_routers, "get_push_settings", lambda: push_settings)

    class _NoOpRepository:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def cleanup(self, *_args, **_kwargs) -> None:
            return None

        async def purge_subscriptions(self, *_args, **_kwargs) -> None:
            return None

        async def generate(self, *_args, **_kwargs) -> None:
            return None

        async def candidate_ids(self, *_args, **_kwargs) -> list[str]:
            return []

    class _NoOpDeliveryService:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def revoke_invalid(self, *_args, **_kwargs) -> None:
            return None

    monkeypatch.setattr(process_push, "PushRepository", _NoOpRepository)
    monkeypatch.setattr(process_push, "PushDeliveryService", _NoOpDeliveryService)

    session_cm = Mock()
    session_cm.__aenter__ = AsyncMock(return_value=Mock())
    session_cm.__aexit__ = AsyncMock(return_value=False)
    factory = Mock()
    factory.begin = Mock(return_value=session_cm)

    assert push_routers.push_settings().enabled
    assert await process_push.process_push_once(settings=push_settings, session_factory=factory) == 0
    factory.begin.assert_called()


async def test_disabled_sender_does_not_touch_database(push_settings):
    push_settings.enabled = False
    factory = Mock()
    assert await process_push.process_push_once(settings=push_settings, session_factory=factory) == 0
    factory.begin.assert_not_called()


async def test_recreated_production_off_settings_do_not_call_sender(push_settings, monkeypatch):
    monkeypatch.setattr(config, "ENV", "production")
    sender = Mock()
    monkeypatch.setattr(process_push, "send_push", sender)
    factory = Mock()
    restarted_settings = push_settings.model_copy(update={"production_enabled": False})

    assert await process_push.process_push_once(settings=restarted_settings, session_factory=factory) == 0
    factory.begin.assert_not_called()
    sender.assert_not_called()


async def test_invalid_configuration_logs_fixed_reason(monkeypatch, caplog):
    def fail():
        raise ValueError("SENSITIVE_SECRET_SENTINEL")

    monkeypatch.setattr(process_push, "get_push_settings", fail)
    assert await process_push.run() is False
    assert "SENSITIVE_SECRET_SENTINEL" not in caplog.text
