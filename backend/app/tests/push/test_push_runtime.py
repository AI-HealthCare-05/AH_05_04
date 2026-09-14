from unittest.mock import Mock

from app.apis.v1 import push_routers
from app.commands import process_push
from app.core import config


async def test_production_cannot_register_or_send_even_when_enabled(push_settings, monkeypatch):
    monkeypatch.setattr(config, "ENV", "production")
    monkeypatch.setattr(push_routers, "get_push_settings", lambda: push_settings)
    factory = Mock()
    assert not push_routers.push_settings().enabled
    assert await process_push.process_push_once(settings=push_settings, session_factory=factory) == 0
    factory.begin.assert_not_called()


async def test_disabled_sender_does_not_touch_database(push_settings):
    push_settings.enabled = False
    factory = Mock()
    assert await process_push.process_push_once(settings=push_settings, session_factory=factory) == 0
    factory.begin.assert_not_called()


async def test_invalid_configuration_logs_fixed_reason(monkeypatch, caplog):
    def fail():
        raise ValueError("SENSITIVE_SECRET_SENTINEL")

    monkeypatch.setattr(process_push, "get_push_settings", fail)
    assert await process_push.run() is False
    assert "SENSITIVE_SECRET_SENTINEL" not in caplog.text
