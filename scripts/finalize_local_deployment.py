"""有边界地切换本项目开发服务并核对原卷、配置及数据库记录；不调用模型。"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import time

import httpx
from sqlalchemy import select, text

from app.config import PROJECT_ROOT, get_settings
from app.database import engine, SessionLocal
from app.models import User
from app.security import create_access_token
from scripts.verify_deployment import docker, wait_ready


def snapshot():
    result = {}
    # 仅固定表名；不将行内容写入文件或日志，只在内存计算摘要比较。
    with engine.connect() as connection:
        for table in ("guides", "users", "conversations", "messages", "reference_documents", "reference_chunks", "alembic_version"):
            key = "version_num" if table == "alembic_version" else "id"
            rows = connection.execute(text(f"SELECT row_to_json(t)::text FROM {table} AS t ORDER BY {key}"))
            digest, count = hashlib.sha256(), 0
            for row in rows:
                digest.update(row[0].encode()); digest.update(b"\n"); count += 1
            result[table] = {"count": count, "digest": digest.hexdigest()}
    return result


def volume_names():
    result = {}
    for service in ("postgres", "redis"):
        container = docker("compose", "ps", "--quiet", service)
        mounts = json.loads(docker("inspect", "--format", "{{json .Mounts}}", container))
        result[service] = [mount["Name"] for mount in mounts if mount["Type"] == "volume"]
        assert result[service], "原数据卷未找到，停止切换"
    return result


def stop_checked_server(pid, port):
    proc = Path(f"/proc/{pid}")
    if proc.stat().st_uid != os.getuid() or Path(os.readlink(proc / "cwd")) != PROJECT_ROOT:
        raise RuntimeError("进程不属于本用户/本项目，拒绝停止")
    args = (proc / "cmdline").read_bytes().decode().strip("\0").split("\0")
    if "uvicorn" not in args or "app.main:app" not in args or "--reload" not in args:
        raise RuntimeError("进程不是已确认的本项目开发服务，拒绝停止")
    configured_port = int(args[args.index("--port") + 1]) if "--port" in args else 8000
    if port != configured_port:
        raise RuntimeError("端口不匹配，拒绝停止")
    with engine.connect() as connection:
        active = connection.scalar(text("SELECT count(*) FROM messages WHERE status='generating' AND generation_expires_at > now()"))
    if active:
        raise RuntimeError("存在正在生成的消息，请等待结束再切换")
    os.kill(pid, signal.SIGINT)
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return
        time.sleep(0.2)
    raise RuntimeError("开发服务尚未释放端口；未停止任何其他进程")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--switch-pid", type=int, help="仅允许本项目、当前用户、指定端口的uvicorn --reload父进程")
    args = parser.parse_args()
    from dotenv import dotenv_values
    port = int(dotenv_values(PROJECT_ROOT / ".env").get("APP_PORT") or "8000")
    config_before = (PROJECT_ROOT / ".env").read_bytes()
    volumes_before = volume_names()
    docker("compose", "config", "--quiet")
    if args.switch_pid:
        stop_checked_server(args.switch_pid, port)
    before = snapshot()
    docker("compose", "up", "--detach", "--build")
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=8) as client:
        wait_ready(client)
        assert client.get("/").status_code == 200
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/guides", params={"keyword": "校园卡"}).json()["total"] >= 1
        config = client.get("/chat/config").json()
        assert config["mode"] == get_settings().model_provider
        protected_read = False
        with SessionLocal() as session:
            user_id = session.scalar(select(User.id).order_by(User.id).limit(1))
        if user_id:
            # 不索取或记录用户密码；仅用本机签名能力做只读受保护路由检查。
            headers = {"Authorization": "Bearer " + create_access_token(user_id)}
            assert client.get("/users/me", headers=headers).status_code == 200
            assert client.get("/conversations", headers=headers).status_code == 200
            references = client.get("/references/search", headers=headers, params={"q": "国家助学金"}).json()["references"]
            original = client.get(references[0]["original_url"], headers=headers)
            assert original.status_code == 200 and hashlib.sha256(original.content).hexdigest() == references[0]["file_sha256"]
            assert client.get(references[0]["original_url"]).status_code == 401
            protected_read = True
        assert snapshot() == before, "初始化后现有表记录发生变化，需要核对"
        docker("compose", "restart", "app")
        wait_ready(client)
        assert snapshot() == before, "重启后表记录摘要发生变化，需要核对"
        if user_id:
            assert client.get("/users/me", headers=headers).status_code == 200
    assert volume_names() == volumes_before
    assert (PROJECT_ROOT / ".env").read_bytes() == config_before
    app_id = docker("compose", "ps", "--quiet", "app")
    health = docker("inspect", "--format", "{{.State.Health.Status}}", app_id)
    deadline = time.monotonic() + 35
    while health != "healthy" and time.monotonic() < deadline:
        time.sleep(1)
        health = docker("inspect", "--format", "{{.State.Health.Status}}", app_id)
    assert health == "healthy"
    migrate = docker("compose", "ps", "--all", "--quiet", "migrate")
    assert docker("inspect", "--format", "{{.State.ExitCode}}", migrate) == "0"
    report = {"url": f"http://localhost:{port}", "application": "healthy", "migrate_exit_code": 0,
              "volumes_preserved": volumes_before, "configuration_bytes_preserved": True,
              "all_existing_table_records_unchanged_after_init_and_restart": True,
              "table_counts": {name: item["count"] for name, item in before.items()},
              "protected_routes_and_original_read": protected_read,
              "active_model_configuration": {key: config[key] for key in ("mode", "model", "ready", "answer_policy_version")},
              "real_api_requests_this_check": 0, "unrelated_processes_stopped": 0}
    (PROJECT_ROOT / "docs" / "local-deployment-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
