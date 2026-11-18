import os
from functools import lru_cache
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


@lru_cache(maxsize=32)
def _fixture_password_hash(password):
    from werkzeug.security import generate_password_hash

    return generate_password_hash(password)


@pytest.fixture
def create_user(app):
    from flask_jwt_extended import create_access_token
    from app.extensions import db
    from app.models import User

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
        user = User(username=username, email=payload["email"],
                    password_hash=_fixture_password_hash(password))
        db.session.add(user)
        db.session.commit()
        return {
            "id": user.id,
            "token": create_access_token(identity=str(user.id)),
            **payload,
        }

    return factory


@pytest.fixture
def auth_headers():
    def factory(user):
        return {"Authorization": f"Bearer {user['token']}"}

    return factory
