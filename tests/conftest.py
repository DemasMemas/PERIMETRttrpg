import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / 'helpers'))
from isolation_guard import memory_database_scope

os.environ['PYTHON_DOTENV_DISABLED'] = '1'

import pytest


@pytest.fixture
def app():
    from app import create_app
    from app.extensions import db

    test_app = create_app("testing")
    with test_app.app_context():
        with memory_database_scope(test_app, db):
            yield test_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def create_user(client):
    sequence = 0

    def factory(username=None, password="secret-123"):
        nonlocal sequence
        sequence += 1
        username = username or f"user-{sequence}"
        payload = {
            "username": username,
            "email": f"{username}@example.com",
            "password": password,
        }
        response = client.post("/auth/register", json=payload)
        assert response.status_code == 201

        login = client.post(
            "/auth/login",
            json={"username": username, "password": password},
        )
        assert login.status_code == 200
        result = login.get_json()
        return {
            "id": result["user_id"],
            "token": result["access_token"],
            **payload,
        }

    return factory


@pytest.fixture
def auth_headers():
    def factory(user):
        return {"Authorization": f"Bearer {user['token']}"}

    return factory
