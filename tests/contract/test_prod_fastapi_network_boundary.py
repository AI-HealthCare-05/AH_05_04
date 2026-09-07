"""운영 FastAPI host 포트로 Nginx internal 차단을 우회하지 못하도록 고정합니다."""

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_prod_fastapi_has_no_host_network_entrypoint() -> None:
    compose = yaml.safe_load((PROJECT_ROOT / "infra/docker/docker-compose.prod.yml").read_text(encoding="utf-8"))
    fastapi = compose["services"]["fastapi"]

    assert not fastapi.get("ports"), "운영 FastAPI 포트를 host에 공개하면 Nginx를 우회할 수 있습니다."
    assert "network_mode" not in fastapi, "FastAPI는 명시적인 Compose 네트워크로만 연결해야 합니다."


def test_prod_nginx_reaches_fastapi_on_shared_bridge_network() -> None:
    compose = yaml.safe_load((PROJECT_ROOT / "infra/docker/docker-compose.prod.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    shared_networks = set(services["fastapi"]["networks"]) & set(services["nginx"]["networks"])

    assert shared_networks, "Nginx와 FastAPI가 통신할 내부 네트워크가 필요합니다."
    for network in services["fastapi"]["networks"]:
        assert compose["networks"][network].get("driver", "bridge") == "bridge"
    assert set(services["nginx"]["ports"]) == {"80:80", "443:443"}
