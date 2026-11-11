"""测试夹具：每个用例前后都把内存仓库重置回种子数据，避免相互污染。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import store


@pytest.fixture
def client() -> TestClient:
    store.reset()
    with TestClient(app) as test_client:
        yield test_client
    store.reset()
