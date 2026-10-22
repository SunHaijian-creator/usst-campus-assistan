"""已构建镜像的隔离部署验收；备用端口、临时库、mock，绝不请求真实模型。"""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
from uuid import uuid4

import httpx

from scripts.test_database import isolated_database
from scripts.verify_auth_http import expect
from scripts.verify_chat_http import event_stream

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def docker(*args, environment=None):
    result = subprocess.run(["docker", *args], cwd=PROJECT_ROOT, env=environment,
                            capture_output=True, text=True, timeout=180)
    if result.returncode:
        # 不打印环境、完整命令或 Compose 渲染结果。
        raise RuntimeError(f"Docker 验收命令失败，退出码 {result.returncode}；请查看对应服务日志")
    return result.stdout.strip()


def wait_ready(client):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        try:
            response = client.get("/ready")
            if response.status_code == 200:
                assert response.json()["database"] == response.json()["redis"] == "ok"
                return
        except httpx.TransportError:
            pass
        time.sleep(0.2)
    raise RuntimeError("测试容器就绪超时")


@contextmanager
def acceptance_container(database):
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    name = "campus-assistant-check-" + uuid4().hex[:12]
    environment = dict(os.environ, JWT_SECRET_KEY=secrets.token_urlsafe(64),
                       POSTGRES_DB=database, MODEL_PROVIDER="mock", MODEL_API_KEY="",
                       MOCK_DELAY_SECONDS="0.04", MODEL_TIMEOUT_SECONDS="30")
    overrides = []
    for key in ("JWT_SECRET_KEY", "POSTGRES_DB", "MODEL_PROVIDER", "MODEL_API_KEY",
                "MOCK_DELAY_SECONDS", "MODEL_TIMEOUT_SECONDS", "GUIDE_CACHE_PREFIX", "CHAT_CONTEXT_PREFIX"):
        overrides.extend(["--env", key])  # 值从子进程环境读取，不出现在命令或日志中。
    docker("compose", "run", "--rm", "--no-deps", "-T", *overrides, "migrate", environment=environment)
    created = False
    try:
        docker("compose", "run", "--detach", "--no-deps", "--name", name,
               "--publish", f"127.0.0.1:{port}:8000", *overrides, "app", environment=environment)
        created = True
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=40, trust_env=False) as client:
            wait_ready(client)
            yield client, name
    finally:
        if created:
            docker("rm", "--force", name)  # 只移除本函数成功创建的临时容器；不操作卷。


def main():
    checks = {}
    with isolated_database() as database:
        try:
            with acceptance_container(database) as (client, container):
                assert expect(client.get("/health"), 200) == {"status": "ok"}
                assert "上理办事助手" in client.get("/").text
                assert expect(client.get("/chat/config"), 200)["mode"] == "mock"
                identity = docker("exec", container, "python", "-c",
                    "import os,pathlib,json; assert os.getuid()!=0; "
                    "assert not pathlib.Path('/app/.env').exists(); "
                    "assert not pathlib.Path('/app/.venv').exists(); "
                    "assert not pathlib.Path('/app/tests').exists(); "
                    "assert os.environ['POSTGRES_HOST']=='postgres'; "
                    "assert os.environ['REDIS_HOST']=='redis'; "
                    "print(json.dumps({'uid':os.getuid(),'secrets_not_in_image':True}))")
                checks["image"] = json.loads(identity)
                credentials = {"username": "check_" + uuid4().hex[:16], "password": secrets.token_urlsafe(24)}
                expect(client.post("/auth/register", json=credentials), 201)
                token = expect(client.post("/auth/token", data=credentials), 200)["access_token"]
                headers = {"Authorization": "Bearer " + token}
                expect(client.get("/users/me", headers=headers), 200)
                checks["register_login"] = True

                guides = expect(client.get("/guides", params={"keyword": "校园卡"}), 200)
                assert guides["total"] >= 1
                guide_id = guides["items"][0]["id"]
                expect(client.get(f"/guides/{guide_id}"), 200)
                documents = expect(client.get("/references/search", headers=headers, params={"q": "国家助学金"}), 200)
                ref = documents["references"][0]
                assert "6000" in ref["excerpt"]
                expect(client.get(ref["original_url"]), 401)
                original = client.get(ref["original_url"], headers=headers)
                assert original.status_code == 200 and hashlib.sha256(original.content).hexdigest() == ref["file_sha256"]
                checks["guides_document_search_authenticated_original"] = True

                cid = expect(client.post("/conversations", headers=headers, json={"title": "隔离部署检查"}), 201)["id"]
                url = f"/conversations/{cid}"
                for index, question in enumerate(("研究生国家助学金是多少", "金额是多少？", "上理怎么样")):
                    with client.stream("POST", url + "/chat", headers=headers, json={"content": question}) as response:
                        events = list(event_stream(response))
                    assert events[-1][0] == "done" and events[0][1]["mode"] == "mock"
                    assert events[0][1]["context_rounds"] == index
                    assert sum(name == "delta" for name, _ in events) > 1
                    if index < 2:
                        assert events[-1][1]["references"]
                    else:
                        assert events[0][1]["references"] == events[-1][1]["references"] == []
                checks["mock_sse_multiturn_actual_citations"] = True

                with client.stream("POST", url + "/chat", headers=headers, json={"content": "学业奖学金金额与比例"}) as response:
                    for name, _ in event_stream(response):
                        if name == "delta":
                            break
                deadline = time.monotonic() + 5
                while True:
                    history = expect(client.get(url + "/messages", headers=headers), 200)
                    if history["items"][-1]["status"] == "cancelled":
                        break
                    assert time.monotonic() < deadline, "客户端断开后未恢复生成状态"
                    time.sleep(0.05)
                checks["mock_stop_generation"] = True
                before = history["items"]
                docker("restart", container)
                wait_ready(client)
                after = expect(client.get(url + "/messages", headers=headers), 200)["items"]
                assert before == after
                expect(client.get("/users/me", headers=headers), 200)
                checks["container_restart_preserves_history_and_token"] = True
        finally:
            # 这些连接只能指向 isolated_database 创建的随机测试库。
            from app.context_cache import get_context_cache
            from app.cache import get_guide_cache
            from app.database import SessionLocal
            from app.models import Conversation, Guide
            from sqlalchemy import select
            with SessionLocal() as session:
                for conversation in session.scalars(select(Conversation)):
                    get_context_cache().delete(conversation.user_id, conversation.id)
                for item_id in session.scalars(select(Guide.id)):
                    get_guide_cache().client.delete(get_guide_cache().key(item_id))
    report = {"scope": "same image; isolated database; temporary application container; mock only",
              "checks": checks, "real_api_requests": 0, "temporary_resources_cleaned": True}
    (PROJECT_ROOT / "docs" / "deployment-acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print("镜像隔离部署验收通过：非root、初始化、登录、指南、鉴权原件、SSE、多轮、停止与重启历史保留")
    print("真实模型请求0次；临时应用、测试库及明确的缓存键已清理；开发服务和数据卷未改动")


if __name__ == "__main__":
    main()
