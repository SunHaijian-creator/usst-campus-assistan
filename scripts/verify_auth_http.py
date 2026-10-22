"""在临时数据库上执行真实 HTTP 流程及应用重启验证，不留下测试账号。"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time

import httpx
import jwt
from alembic import command
from alembic.config import Config

from app.config import PROJECT_ROOT, get_settings
from scripts.test_database import isolated_database


@contextmanager
def running_server(overrides=None):
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    with tempfile.TemporaryFile(mode="w+t") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port), "--reload"],
            cwd=PROJECT_ROOT, env=dict(os.environ, **(overrides or {})), stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5) as client:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("Uvicorn 启动失败")
                    try:
                        response = client.get("/health")
                        if response.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError("等待应用启动超时")
                yield client
        except BaseException:
            log.seek(0)
            print(log.read(), file=sys.stderr)
            raise
        finally:
            try:
                os.killpg(process.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


def expect(response, status):
    assert response.status_code == status, f"HTTP 状态码应为 {status}，实际为 {response.status_code}"
    return response.json() if response.content else None


def main() -> None:
    with isolated_database():
        command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
        from scripts.import_demo_guides import import_demo_guides
        assert import_demo_guides() == 8
        password = secrets.token_urlsafe(24)
        with running_server() as client:
            expect(client.get("/users/me"), 401)
            account = {"username": "http_demo_user", "password": password}
            user = expect(client.post("/auth/register", json=account), 201)
            assert set(user) == {"id", "username", "created_at"}
            expect(client.post("/auth/register", json=account), 409)
            token = expect(client.post("/auth/token", data=account), 200)["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            assert expect(client.get("/users/me", headers=headers), 200) == user
            conversation = expect(client.post("/conversations", headers=headers, json={"title": "校园卡咨询（HTTP 验收）"}), 201)
            url = f"/conversations/{conversation['id']}"
            saved = [
                expect(client.post(url + "/messages", headers=headers, json={"content": content}), 201)
                for content in ("我想补办校园卡", "请保存这条历史消息")
            ]
            assert all(item["role"] == "user" for item in saved)
            expect(client.post(url + "/messages", headers=headers, json={"content": "冒充", "role": "assistant"}), 422)
            assert expect(client.get("/conversations", headers=headers), 200)["total"] == 1
            assert expect(client.get(url, headers=headers), 200)["id"] == conversation["id"]
            history = expect(client.get(url + "/messages?page_size=1", headers=headers), 200)
            assert history["total"] == 2 and history["items"][0]["id"] == saved[0]["id"]
            second = {"username": "http_other_user", "password": secrets.token_urlsafe(24)}
            expect(client.post("/auth/register", json=second), 201)
            other_token = expect(client.post("/auth/token", data=second), 200)["access_token"]
            other_headers = {"Authorization": f"Bearer {other_token}"}
            expect(client.get(url, headers=other_headers), 404)
            expect(client.post(url + "/messages", headers=other_headers, json={"content": "越权写入"}), 404)
            now = datetime.now(timezone.utc)
            expired = jwt.encode(
                {"sub": user["id"], "iat": now - timedelta(minutes=2), "exp": now - timedelta(seconds=1),
                 "iss": "campus-assistant", "aud": "campus-assistant-api", "type": "access"},
                get_settings().jwt_secret_key.get_secret_value(), algorithm="HS256",
            )
            expect(client.get("/users/me", headers={"Authorization": f"Bearer {expired}"}), 401)
            assert expect(client.get("/guides"), 200)["total"] == 8
            print("真实 HTTP：注册、重复注册、登录、当前用户、会话、消息分页、越权 404、过期令牌均通过")

        with running_server() as client:
            assert expect(client.get("/users/me", headers=headers), 200) == user
            history = expect(client.get(url + "/messages", headers=headers), 200)
            assert history["items"] == saved
            expect(client.delete(url, headers=headers), 204)
            expect(client.get(url, headers=headers), 404)
            expect(client.get(url + "/messages", headers=headers), 404)
            print("重启验收：原令牌仍有效，历史消息完整保留；删除会话及消息验证通过")
    print("临时应用已停止，独立测试数据库已删除；未写入开发库，也未修改 Redis")


if __name__ == "__main__":
    main()
