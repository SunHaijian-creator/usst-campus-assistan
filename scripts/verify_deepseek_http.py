"""DeepSeek 协议的本机 HTTP / 浏览器验收；上游完全模拟，不访问真实 API。"""

import argparse
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import threading
import time
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.config import PROJECT_ROOT
from scripts.test_database import isolated_database
from scripts.verify_auth_http import expect, running_server
from scripts.verify_chat_http import event_stream

FAKE_KEY = "local-protocol-test-only"


def chunk(content=None, finish=None):
    return ("data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": content},
                                               "finish_reason": finish}]}, ensure_ascii=False) + "\n\n").encode()


@contextmanager
def fake_upstream():
    requests, closed = [], threading.Event()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            assert self.path == "/chat/completions"
            assert self.headers["Authorization"] == "Bearer " + FAKE_KEY
            question = body["messages"][-1]["content"]
            self.send_response(401 if question == "__auth__" else 200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                if question == "__auth__":
                    return
                for part in ["【本机模拟上游，非真实 API 验收】", "演示数据（虚构），", "不代表真实校园政策。"]:
                    self.wfile.write(chunk(part))
                    self.wfile.flush()
                    time.sleep(0.08)
                references = json.loads(body["messages"][1]["content"])["reference_data"]
                if references:
                    self.wfile.write(chunk(f"[{references[0]['id']}]"))
                    self.wfile.flush()
                if question in ("__cancel__", "__timeout__"):
                    while True:
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                        time.sleep(0.03)
                if question == "__break__":
                    return
                self.wfile.write(chunk(finish="length" if question == "__length__" else "stop") + b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                closed.set()
            finally:
                self.close_connection = True

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests, closed
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def migration_check():
    """在临时库先创建第四阶段记录，再做增量升级核对保留与标记。"""
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(config, "0003_streaming_chat")
    from app.database import SessionLocal
    from scripts.import_demo_guides import import_demo_guides
    assert import_demo_guides() == 8
    uid, cid = uuid4(), uuid4()
    with SessionLocal.begin() as session:
        session.execute(text("INSERT INTO users (id,username,hashed_password) VALUES (:id,'legacy_migration_test','unused-test-hash')"), {"id": uid})
        session.execute(text("INSERT INTO conversations (id,user_id,title) VALUES (:id,:uid,'旧会话')"), {"id": cid, "uid": uid})
        session.execute(text("INSERT INTO messages (conversation_id,role,content) VALUES (:id,'assistant','旧模拟回复')"), {"id": cid})
        before = session.execute(text("SELECT id,content,created_at,status FROM messages")).all()
        guides = session.execute(text("SELECT * FROM guides ORDER BY id")).all()
    command.upgrade(config, "head")
    command.check(config)
    with SessionLocal.begin() as session:
        assert session.execute(text("SELECT id,content,created_at,status FROM messages")).all() == before
        assert session.execute(text("SELECT * FROM guides ORDER BY id")).all() == guides
        assert session.execute(text("SELECT model_provider,model_name FROM messages")).one() == ("mock", "mock-campus-v1")
        session.execute(text("DELETE FROM users WHERE id=:id"), {"id": uid})
    print("增量迁移通过：旧消息内容、时间、状态和 8 条指南保留；旧回复标记为 mock")


def browser_check(client, username, password):
    from playwright.sync_api import sync_playwright, expect as browser_expect

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(type(error).__name__))
        try:
            page.goto(str(client.base_url))
            browser_expect(page.locator("#mode")).to_have_text("DeepSeek · deepseek-flash")
            assert page.locator("#scenario-label").is_hidden()
            page.locator("#username").fill(username)
            page.locator("#password").fill(password)
            page.get_by_role("button", name="登录", exact=True).click()
            page.locator("#account").wait_for(state="visible")
            page.locator("#new-conversation").click()
            browser_expect(page.locator("#status")).to_contain_text("会话已创建")
            page.locator("#question").fill("校园卡补办需要什么材料？")
            page.locator("#send").click()
            browser_expect(page.locator(".assistant .message-body").last).to_contain_text("本机模拟上游")
            browser_expect(page.locator(".assistant[data-status=completed]")).to_have_count(1)
            browser_expect(page.locator(".assistant .message-label").last).to_contain_text("DeepSeek · deepseek-flash")
            browser_expect(page.locator(".references").last).to_contain_text("演示数据")
            page.reload()
            browser_expect(page.locator(".assistant[data-status=completed]")).to_have_count(1)
            browser_expect(page.locator(".assistant .message-label").last).to_contain_text("DeepSeek · deepseek-flash")
            page.locator("#question").fill("__auth__")
            page.locator("#send").click()
            browser_expect(page.locator("#status")).to_contain_text("本站登录仍有效")
            browser_expect(page.locator(".assistant[data-status=failed]")).to_have_count(1)
            browser_expect(page.locator("#account")).to_be_visible()
            assert page.locator("#send").is_enabled()
            assert not errors
            print("浏览器通过：DeepSeek 模式与模型、隐藏 mock 故障选项、增量、引用、刷新、上游 401 不退出本站登录")
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()
    with isolated_database():
        migration_check()
        from app.context_cache import get_context_cache
        from app.database import SessionLocal
        from app.models import Conversation
        from sqlalchemy import select

        with fake_upstream() as (base, requests, closed):
            options = {"MODEL_PROVIDER": "deepseek", "MODEL_NAME": "deepseek-flash", "MODEL_API_KEY": FAKE_KEY,
                       "MODEL_BASE_URL": base, "MODEL_MAX_TOKENS": "1024", "MODEL_TIMEOUT_SECONDS": "1"}
            try:
                with running_server(options) as client:
                    credentials = {"username": "deepseek_http_test", "password": secrets.token_urlsafe(24)}
                    expect(client.post("/auth/register", json=credentials), 201)
                    token = expect(client.post("/auth/token", data=credentials), 200)["access_token"]
                    headers = {"Authorization": f"Bearer {token}"}
                    cid = expect(client.post("/conversations", headers=headers, json={}), 201)["id"]
                    url = f"/conversations/{cid}/chat"

                    def send(content):
                        with client.stream("POST", url, headers=headers, json={"content": content}) as response:
                            return list(event_stream(response))

                    first = send("校园卡补办需要什么材料？")
                    assert first[-1][0] == "done" and first[0][1]["mode"] == "deepseek"
                    second = send("还需要什么材料？")
                    assert second[-1][0] == "done" and second[0][1]["context_rounds"] == 1
                    assert first[0][1]["references"] == second[0][1]["references"]
                    assert requests[-1]["messages"][-1]["content"] == "还需要什么材料？"
                    for question, code in [("__auth__", "upstream_authentication"), ("__length__", "output_truncated"),
                                           ("__break__", "upstream_incomplete"), ("__timeout__", "timeout")]:
                        result = send(question)
                        assert result[-1][0] == "error" and result[-1][1]["code"] == code
                        assert not any(name == "done" for name, _ in result)
                    expect(client.get("/users/me", headers=headers), 200)
                    closed.clear()
                    with client.stream("POST", url, headers=headers, json={"content": "__cancel__"}) as response:
                        for name, _ in event_stream(response):
                            if name == "delta":
                                break
                    assert closed.wait(2), "取消后必须关闭上游连接"
                    deadline = time.monotonic() + 2
                    while True:
                        rows = expect(client.get(f"/conversations/{cid}/messages", headers=headers), 200)["items"]
                        if rows[-1]["status"] == "cancelled":
                            break
                        assert time.monotonic() < deadline
                        time.sleep(0.03)
                    assert send("校园卡补办")[-1][0] == "done"
                    assert len(requests) == 8, "首版不能自动重试任何上游请求"
                    print("本机 HTTP 通过：正常、多轮、鉴权错误、截断、中断、超时、客户端取消关闭上游、状态保存、无自动重试")
                    if args.browser:
                        browser_check(client, credentials["username"], credentials["password"])
            finally:
                with SessionLocal() as session:
                    for conversation in session.scalars(select(Conversation)):
                        get_context_cache().delete(conversation.user_id, conversation.id)
        print("真实 DeepSeek API 请求数：0；测试数据库与明确的上下文缓存键已清理")


if __name__ == "__main__":
    main()
