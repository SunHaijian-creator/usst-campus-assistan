"""上理主题桌面/手机浏览器验收及截图；隔离数据、仅使用 mock，不调用真实模型。"""

import secrets

from alembic import command
from alembic.config import Config

from app.config import PROJECT_ROOT
from scripts.test_database import isolated_database
from scripts.verify_auth_http import running_server, expect
from scripts.browser_artifacts import save_screenshot


def run_browser(client):
    from playwright.sync_api import sync_playwright, expect as see

    output = PROJECT_ROOT / "docs" / "screenshots"
    output.mkdir(exist_ok=True)
    password = secrets.token_urlsafe(24)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for width, height, device in [(1440, 960, "desktop"), (390, 844, "mobile")]:
                context = browser.new_context(viewport={"width": width, "height": height}, is_mobile=device == "mobile", has_touch=device == "mobile")
                context.grant_permissions(["clipboard-read", "clipboard-write"], origin=str(client.base_url).rstrip("/"))
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(type(error).__name__))
                page.goto(str(client.base_url))
                page.get_by_role("button", name="账号登录", exact=True).wait_for()
                assert "上理办事助手" in page.title()
                assert "统一身份认证" in page.locator(".auth-account-note").inner_text()
                assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
                save_screenshot(page, output / f"usst-login-{device}.png")

                # 实际通过注册页创建项目账号，同时验证登录错误反馈。
                username = f"theme_{device}"
                page.locator("#show-register").click()
                page.locator("#username").fill(username)
                page.locator("#password").fill(password)
                page.locator("#register").click()
                see(page.locator("#auth-status")).to_contain_text("注册成功")
                page.locator("#password").fill("wrong-test-password")
                page.locator("#login-submit").click()
                see(page.locator("#auth-status")).to_have_class("auth-status error")
                assert page.locator("#auth-view").is_visible()
                page.locator("#password").fill(password)
                page.locator("#login-submit").click()
                page.locator("#app-shell").wait_for(state="visible")
                see(page.locator("#welcome")).to_be_visible()
                see(page.locator("#mode")).to_have_text("模拟模型 · mock-campus-v1")
                assert page.evaluate("() => [...document.images].every(i => i.complete && i.naturalWidth > 0)")
                save_screenshot(page, output / f"usst-welcome-{device}.png")

                if device == "mobile":
                    page.locator("#sidebar-open").click()
                    see(page.locator("#sidebar")).to_have_attribute("aria-modal", "true")
                    see(page.locator("#sidebar")).to_have_css("transform", "matrix(1, 0, 0, 1, 0, 0)")
                    see(page.locator("#sidebar-close")).to_be_focused()
                    page.keyboard.press("Shift+Tab")
                    see(page.locator("#logout")).to_be_focused()
                    page.keyboard.press("Tab")
                    see(page.locator("#sidebar-close")).to_be_focused()
                    save_screenshot(page, output / "usst-drawer-mobile.png")
                    page.keyboard.press("Escape")
                    see(page.locator("#sidebar-open")).to_be_focused()
                    page.locator("#sidebar-open").click()
                page.locator("#new-conversation").click()
                see(page.locator("#status")).to_contain_text("会话已创建")
                if device == "mobile":
                    see(page.locator("#sidebar-open")).to_have_attribute("aria-expanded", "false")
                page.locator(".quick-question").first.click()
                see(page.locator("#question")).to_have_value("校园卡补办需要什么材料？")
                assert page.locator(".message").count() == 0, "快捷卡片只填入问题，不暗中调用模型"
                page.locator("#send").click()
                see(page.locator("#stop")).to_be_enabled()
                see(page.locator(".assistant .message-body").last).to_contain_text("模拟回复")
                see(page.locator(".assistant[data-status=completed]")).to_have_count(1)
                see(page.locator("#status")).to_contain_text("已完成并保存")
                first_answer = page.locator(".assistant .message-body").last.inner_text()
                source = page.locator(".references").last
                assert not source.evaluate("el => el.open")
                source.locator("summary").click()
                see(source.locator(".reference-title")).to_contain_text("校园卡补办")
                source.locator(".guide-button").click()
                see(page.locator("#guide-dialog-title")).to_have_text("校园卡补办")
                see(page.locator("#guide-content")).to_contain_text("有效身份证件")
                see(page.locator("#guide-content")).to_contain_text("演示数据")
                save_screenshot(page, output / f"usst-guide-{device}.png")
                page.locator("#guide-close").click()
                if device == "mobile":
                    source.locator(".guide-button").scroll_into_view_if_needed()
                    save_screenshot(page, output / "usst-sources-mobile.png")
                page.locator(".copy-button").last.click()
                see(page.locator("#status")).to_contain_text("复制成功")
                assert page.evaluate("() => navigator.clipboard.readText()") == first_answer
                page.locator("#messages").evaluate("el => {el.scrollTop = 0}")
                save_screenshot(page, output / f"usst-chat-{device}.png")
                cid = page.evaluate("() => sessionStorage.getItem('campus_conversation')")
                if device == "mobile":
                    page.locator("#sidebar-open").click()
                page.locator("#new-conversation").click()
                see(page.locator("#welcome")).to_be_visible()
                if device == "mobile":
                    page.locator("#sidebar-open").click()
                page.locator(f'.conversation[data-id="{cid}"]').click()
                see(page.locator(".assistant[data-status=completed]")).to_have_count(1)
                page.reload()
                see(page.locator(".assistant[data-status=completed]")).to_have_count(1)
                assert page.locator(".assistant .message-body").last.inner_text() == first_answer
                assert page.locator(".assistant .message-avatar").get_attribute("src") == "/static/assets/assistant.svg"

                # 通过真实接口加入足够长的用户历史，实测向上阅读时新分段不抢滚动位置。
                headers = {"Authorization": "Bearer " + expect(client.post("/auth/token", data={"username": username, "password": password}), 200)["access_token"]}
                cid = page.evaluate("() => sessionStorage.getItem('campus_conversation')")
                for n in range(3):
                    expect(client.post(f"/conversations/{cid}/messages", headers=headers, json={"content": f"历史滚动验证 {n + 1}：\n" + "这是一条隔离测试消息，用来检查向上阅读历史时的位置。\n" * 10}), 201)
                page.locator("#refresh").click()
                see(page.locator(".user")).to_have_count(4)
                if page.locator("#jump-bottom").is_visible():
                    page.locator("#jump-bottom").click()
                page.locator("#question").fill("还需要什么材料？")
                page.locator("#question").press("Enter")
                see(page.locator(".assistant")).to_have_count(2)
                page.locator("#messages").evaluate("el => {el.scrollTop = 0}")
                see(page.locator("#jump-bottom")).to_be_visible()
                see(page.locator(".assistant[data-status=completed]")).to_have_count(2)
                assert page.locator("#messages").evaluate("el => el.scrollTop") < 5, "生成结束回读历史也不能强制滚到底部"
                page.locator("#jump-bottom").click()
                see(page.locator("#jump-bottom")).to_be_hidden()

                # 停止生成、失败、超时与 HTTP 错误均实际执行。
                page.locator("#scenario").select_option("timeout")
                page.locator("#question").fill("校园卡补办，测试停止")
                page.locator("#send").click()
                see(page.locator(".assistant")).to_have_count(3)
                see(page.locator(".assistant .message-body").last).to_contain_text("模拟回复")
                page.locator("#stop").click()
                see(page.locator(".assistant[data-status=cancelled]")).to_have_count(1)
                for scenario, text in [("failure", "失败"), ("timeout", "超时")]:
                    page.locator("#scenario").select_option(scenario)
                    page.locator("#question").fill("校园卡补办")
                    page.locator("#send").click()
                    see(page.locator("#status")).to_contain_text(text, timeout=10000)
                    see(page.locator("#send")).to_be_enabled()
                see(page.locator(".assistant[data-status=failed]")).to_have_count(2)
                page.route("**/conversations/*/chat", lambda route: route.fulfill(status=503, content_type="application/json", body='{"detail":"请求失败测试，请稍后重试"}'))
                page.locator("#scenario").select_option("normal")
                page.locator("#question").fill("请求失败测试")
                page.locator("#send").click()
                see(page.locator("#status")).to_contain_text("请求失败测试")
                see(page.locator("#question")).to_have_value("请求失败测试")
                page.unroute("**/conversations/*/chat")
                # 来源详情请求失败不影响聊天与账号。
                page.locator(".references").last.locator("summary").click()
                page.route("**/guides/*", lambda route: route.abort())
                page.locator(".references").last.locator(".guide-button").click()
                see(page.locator("#guide-content")).to_contain_text("指南加载失败")
                page.unroute("**/guides/*")
                page.get_by_role("button", name="重新加载指南").click()
                see(page.locator("#guide-dialog-title")).to_have_text("校园卡补办")
                page.locator("#guide-close").click()
                for test_width, test_height in ([(320, 740), (390, 844), (768, 1024)] if device == "mobile" else [(1024, 768), (1440, 960)]):
                    page.set_viewport_size({"width": test_width, "height": test_height})
                    assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"), "禁止横向溢出"
                    for selector in ["#question", "#send", "#stop"]:
                        box = page.locator(selector).bounding_box()
                        assert box and box["x"] >= 0 and box["x"] + box["width"] <= test_width + 1
                        assert box["y"] >= 0 and box["y"] + box["height"] <= test_height + 1
                assert not errors, "页面不能出现未处理脚本异常"
                print(f"{device} 通过：注册/登录错误、创建、快捷问题、增量、引用/详情、复制、刷新、滚动保持、停止、失败、超时、请求错误、响应式布局")
                context.close()
        finally:
            browser.close()
    print("截图保存到 docs/screenshots/；真实模型请求 0 次")


def main():
    with isolated_database():
        command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
        from scripts.import_demo_guides import import_demo_guides
        from app.context_cache import get_context_cache
        from app.database import SessionLocal
        from app.models import Conversation
        from sqlalchemy import select
        assert import_demo_guides() == 8
        try:
            with running_server({"MODEL_PROVIDER": "mock", "MODEL_API_KEY": "", "MODEL_TIMEOUT_SECONDS": "2.8", "MOCK_CHUNK_CHARS": "16", "MOCK_DELAY_SECONDS": "0.04"}) as client:
                run_browser(client)
        finally:
            with SessionLocal() as session:
                for conversation in session.scalars(select(Conversation)):
                    get_context_cache().delete(conversation.user_id, conversation.id)
    print("测试库、临时进程及明确的 Redis 测试键已清理，开发数据保留")


if __name__ == "__main__":
    main()
