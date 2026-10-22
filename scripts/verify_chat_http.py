"""真实 HTTP / 浏览器聊天验收，所有账号、历史均在自动清理的临时数据库。"""

import argparse
import json
from pathlib import Path
import secrets
import socket
import time
import re

from alembic import command
from alembic.config import Config

from app.config import PROJECT_ROOT
from scripts.test_database import isolated_database
from scripts.verify_auth_http import expect, running_server


def event_stream(response):
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    event, data = "message", []
    for line in response.iter_lines():
        if line == "":
            if data:
                yield event, json.loads("\n".join(data))
            event, data = "message", []
        elif line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            data.append(line[6:])


def chat(client, url, headers, scenario="normal", content="校园卡补办需要什么材料？"):
    with client.stream("POST", url + "/chat", headers=headers, json={"content": content, "mock_scenario": scenario}) as response:
        return list(event_stream(response))


def browser_flow(base_url, username, password):
    from playwright.sync_api import sync_playwright, expect as browser_expect

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1260, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(str(base_url))
            page.locator("#username").fill(username)
            page.locator("#password").fill(password)
            page.get_by_role("button", name="登录", exact=True).click()
            page.locator("#account").wait_for(state="visible")
            page.locator("#new-conversation").click()
            browser_expect(page.locator("#status")).to_contain_text("会话已创建")
            page.locator("#question").fill("校园卡补办需要什么材料？")
            page.locator("#send").click()
            browser_expect(page.locator(".assistant .message-body").last).to_have_text(re.compile(r"^【模拟"))
            assert page.locator("#stop").is_enabled()
            partial = page.locator(".assistant .message-body").last.text_content()
            browser_expect(page.locator("#status")).to_contain_text("已完成并保存")
            page.locator(".assistant[data-status=completed]").wait_for()
            final = page.locator(".assistant .message-body").last.text_content()
            assert len(final) > len(partial), "页面应在生成完成之前显示增量文本"
            assert "校园卡补办" in page.locator(".references").last.text_content()
            assert "演示数据" in page.locator(".references").last.text_content()
            page.reload()
            page.locator(".assistant[data-status=completed]").wait_for()
            assert page.locator(".assistant .message-body").last.text_content() == final
            page.locator("#question").fill("还需要什么材料？")
            page.locator("#send").click()
            browser_expect(page.locator(".assistant[data-status=completed]")).to_have_count(2)
            browser_expect(page.locator("#status")).to_contain_text("已完成并保存")
            assert "包含 1 轮" in page.locator(".assistant .message-body").last.text_content()

            page.locator("#scenario").select_option("timeout")
            page.locator("#question").fill("校园卡补办，演示停止生成")
            page.locator("#send").click()
            browser_expect(page.locator(".assistant")).to_have_count(3)
            browser_expect(page.locator("#stop")).to_be_enabled()
            page.locator("#stop").click()
            page.locator(".assistant[data-status=cancelled]").wait_for()
            page.reload()
            page.locator(".assistant[data-status=cancelled]").wait_for()
            assert page.locator(".assistant[data-status=completed]").count() == 2

            # 真正执行页面使用的解析器：UTF-8 逐字节分块、CRLF 跨块、多事件同块、多行 data。
            parsed = page.evaluate("""async () => {
              const {readSSE, SSEParser} = await import('/static/sse.js');
              const source = ': ping\\r\\nevent: meta\\r\\ndata: {"n":1}\\r\\n\\r\\nevent: delta\\ndata: {"text":"你好😀"}\\n\\nevent: done\\ndata: {\\ndata: "ok":true}\\n\\n';
              const bytes = new TextEncoder().encode(source), results = [];
              for (const width of [1, 2, 7, bytes.length]) {
                const got = [];
                const body = new ReadableStream({start(controller) {
                  for (let i=0;i<bytes.length;i+=width) controller.enqueue(bytes.slice(i,i+width));
                  controller.close();
                }});
                await readSSE(body, (event, data) => got.push({event, data}));
                results.push(got);
              }
              const incomplete = [];
              const parser = new SSEParser(frame => incomplete.push(frame));
              parser.feed('event: delta\\ndata: {"text":"unfinished"}'); parser.finish();
              return {results, incomplete};
            }""")
            expected = [{"event": "meta", "data": {"n": 1}}, {"event": "delta", "data": {"text": "你好😀"}}, {"event": "done", "data": {"ok": True}}]
            assert all(result == expected for result in parsed["results"])
            assert parsed["incomplete"] == []
            assert not errors, "浏览器不应有未处理的 JavaScript 异常"
            page.screenshot(path="/tmp/campus-assistant-chat.png", full_page=True)
            page.once("dialog", lambda dialog: dialog.accept())
            page.locator("#delete-conversation").click()
            browser_expect(page.locator("#status")).to_contain_text("已删除")
            print("浏览器通过：登录、新建/选择、增量显示、参考资料、多轮、停止、刷新恢复和 SSE 分块解析")
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", action="store_true", help="同时使用 Chromium 验收聊天页面")
    args = parser.parse_args()
    with isolated_database():
        command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
        from scripts.import_demo_guides import import_demo_guides
        from app.context_cache import get_context_cache
        from app.database import SessionLocal
        from app.models import Conversation
        from sqlalchemy import select, text
        assert import_demo_guides() == 8
        options = {"MOCK_DELAY_SECONDS": "0.02", "MOCK_CHUNK_CHARS": "32", "MODEL_TIMEOUT_SECONDS": "1.5"}
        try:
            with running_server(options) as client:
                credentials = {"username": "http_chat_user", "password": secrets.token_urlsafe(24)}
                expect(client.post("/auth/register", json=credentials), 201)
                token = expect(client.post("/auth/token", data=credentials), 200)["access_token"]
                headers = {"Authorization": f"Bearer {token}"}
                conversation = expect(client.post("/conversations", headers=headers, json={}), 201)
                url = f"/conversations/{conversation['id']}"
                first = chat(client, url, headers)
                assert first[0][0] == "meta" and first[-1][0] == "done"
                assert first[0][1]["references"][0]["is_demo"]
                followup = chat(client, url, headers, content="还需要什么材料？")
                assert followup[0][1]["context_rounds"] == 1 and followup[-1][0] == "done"
                for scenario in ("failure", "timeout"):
                    result = chat(client, url, headers, scenario=scenario)
                    assert result[-1][0] == "error" and not any(name == "done" for name, data in result)
                # 收到首段之后实际关闭 TCP/HTTP 流，同时验证 409 和无长事务。
                with client.stream("POST", url + "/chat", headers=headers, json={"content": "校园卡补办", "mock_scenario": "timeout"}) as response:
                    stream = event_stream(response)
                    assert next(stream)[0] == "meta" and next(stream)[0] == "delta"
                    expect(client.post(url + "/chat", headers=headers, json={"content": "并发"}), 409)
                    expect(client.delete(url, headers=headers), 409)
                    with SessionLocal() as session:
                        assert session.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND state = 'idle in transaction' AND pid <> pg_backend_pid()")) == 0
                deadline = time.monotonic() + 5
                while True:
                    history = expect(client.get(url + "/messages", headers=headers), 200)["items"]
                    if history[-1]["status"] == "cancelled": break
                    assert time.monotonic() < deadline, "断开后生成占用应及时释放"
                    time.sleep(0.05)
                result = chat(client, url, headers)
                assert result[-1][0] == "done" and result[0][1]["context_rounds"] == 2
                print("真实 HTTP 通过：正常/多轮 SSE、失败、超时、断开取消、409、无流式长事务")
                if args.browser:
                    browser_flow(client.base_url, credentials["username"], credentials["password"])

            with socket.socket() as refused:
                refused.bind(("127.0.0.1", 0))
                with running_server(dict(options, REDIS_PORT=str(refused.getsockname()[1]))) as client:
                    result = chat(client, url, headers)
                    assert result[-1][0] == "done" and result[0][1]["context_rounds"] == 3
                    print("真实 Redis 连接拒绝通过：应用可启动，上下文从 PostgreSQL 恢复并完成回复")
            with running_server(options) as client:
                history = expect(client.get(url + "/messages", headers=headers), 200)["items"]
                assert any(item["status"] == "failed" for item in history)
                assert any(item["status"] == "cancelled" for item in history)
                expect(client.delete(url, headers=headers), 204)
                expect(client.get(url + "/messages", headers=headers), 404)
                print("重启恢复通过：已完成、失败、取消状态均保留，删除后历史返回 404")
        finally:
            # 从本次临时库列出明确的键；不扫描、清空或批量匹配 Redis 业务键。
            cache = get_context_cache()
            with SessionLocal() as session:
                pairs = session.execute(select(Conversation.user_id, Conversation.id)).all()
            for user_id, conversation_id in pairs:
                cache.delete(user_id, conversation_id)
            cache.client.close()
            get_context_cache.cache_clear()
    print("验收临时进程、测试数据库和明确测试键已清理，开发数据保留")


if __name__ == "__main__":
    main()
