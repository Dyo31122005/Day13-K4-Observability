from fastapi.testclient import TestClient

from app.main import app


def test_dashboard_is_available_from_the_api_app() -> None:
    client = TestClient(app)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Observability" in response.text


def test_dashboard_data_is_available_from_the_api_app() -> None:
    client = TestClient(app)

    response = client.get("/api/data")

    assert response.status_code == 200
    assert {"latency", "traffic", "errors", "cost", "tokens", "quality"}.issubset(
        response.json()
    )
