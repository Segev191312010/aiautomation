from fastapi.testclient import TestClient

from main import app


def test_legacy_trading_frontend_is_not_active():
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/trading")

    assert response.status_code == 404


def test_canonical_dashboard_route_remains_active():
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/app")

    assert response.status_code == 200
