from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_nginx_served_document_has_local_only_csp():
    config = (ROOT / "dashboard" / "nginx.conf").read_text(encoding="utf-8")

    assert 'add_header Content-Security-Policy "default-src \'self\'' in config
    assert "script-src 'self'" in config
    assert "connect-src 'self'" in config
    assert "frame-ancestors 'none'" in config
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in config
    assert "$proxy_add_x_forwarded_for" not in config


def test_compose_dashboard_and_session_proxy_are_loopback_scoped():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert '127.0.0.1:${DASHBOARD_PORT:-3000}:80' in compose
    assert 'TRADEBOT_TRUST_PROXY_HEADERS: "true"' in compose
