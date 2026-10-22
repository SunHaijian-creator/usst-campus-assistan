"""三份用户文档的本机 HTTP / 浏览器离线验收；只用 mock，不调用真实模型。"""

import hashlib
import json
from pathlib import Path
import re
import secrets
from uuid import UUID

from alembic import command
from alembic.config import Config

from app.config import PROJECT_ROOT
from scripts.test_database import isolated_database
from scripts.verify_auth_http import expect, running_server
from scripts.verify_chat_http import event_stream
from scripts.browser_artifacts import save_screenshot

CASES = [
    ("学业奖学金第二阶段二等的金额和比例？", "masters_financial_aid_2024", "8000", False),
    ("国家助学金一年多少钱？", "masters_financial_aid_2024", "6000", False),
    ("奖学金公示需要多久？", "masters_financial_aid_2024", "公示", False),
    ("中期考核小组成员有什么职称要求？", "midterm_assessment", "3名以上", False),
    ("审核后修改开题信息怎么办？", "proposal_submission", "置未审核", False),
    ("中期考核最低多少学分？", "midterm_assessment", "", True),
    ("中期考核截止日期是什么时候？", "midterm_assessment", "", True),
    ("开题完整流程步骤是什么？", "proposal_submission", "", True),
    ("国家助学金每月多少钱？", "masters_financial_aid_2024", "6000", True),
    ("2026年最新学业奖学金标准？", "masters_financial_aid_2024", "", True),
]


def browser_flow(client, credentials):
    from playwright.sync_api import sync_playwright, expect as see
    folder = PROJECT_ROOT / "docs" / "screenshots"
    folder.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for width, height, device in [(1440, 960, "desktop"), (390, 844, "mobile")]:
                context = browser.new_context(viewport={"width": width, "height": height}, accept_downloads=True)
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(type(error).__name__))
                page.goto(str(client.base_url))
                page.locator("#username").fill(credentials["username"])
                page.locator("#password").fill(credentials["password"])
                page.locator("#login-submit").click()
                page.locator("#app-shell").wait_for(state="visible")
                if device == "mobile":
                    page.locator("#sidebar-open").click()
                page.locator("#new-conversation").click()
                see(page.locator("#status")).to_contain_text("会话已创建")
                # 新快捷问题只填入输入框，按发送才执行 mock 聊天。
                page.get_by_role("button", name="2024 奖助学金", exact=True).click()
                see(page.locator("#question")).to_have_value("学业奖学金的金额和比例是什么？")
                page.locator("#send").click()
                see(page.locator(".assistant[data-status=completed]").last).to_be_visible(timeout=15000)
                see(page.locator(".assistant .message-body").last).to_contain_text("依据所提供的2024年文件，当前适用性尚未核验")
                group = page.locator(".references").last
                group.locator("summary").click()
                see(group).to_contain_text("用户提供资料")
                see(group.locator(".reference-table")).to_contain_text("12000")
                assert group.locator(".demo-tag").count() == 0, "用户提供文档不能标为虚构演示指南"
                # PDF 使用带鉴权的 fetch 获取，再打开本机 blob，不把令牌放进 URL。
                downloads = []
                context.on("page", lambda child: child.on("download", lambda item: downloads.append(item)))
                with page.expect_popup() as popup_info:
                    group.locator(".original-button").first.click()
                popup = popup_info.value
                # 在无头 Chromium 中 PDF 可能转为下载；允许 blob 导航或下载事件。
                for _ in range(50):
                    if popup.is_closed() or popup.url.startswith("blob:") or downloads:
                        break
                    page.wait_for_timeout(100)
                if not popup.is_closed() and not popup.url.startswith("blob:"):
                    see(popup.locator(".original-fallback-link")).to_have_attribute("href", re.compile(r"^blob:"))
                    if not downloads:
                        with popup.expect_download() as pdf_download:
                            popup.locator(".original-fallback-link").click()
                        downloads.append(pdf_download.value)
                if downloads:
                    from app.reference_loader import REFERENCE_ROOT
                    pdf_download = downloads[0]
                    # 原生 PDF 下载器可能把 blob UUID 作为建议文件名；校验内容而不是该临时名称。
                    expected_pdf = next((REFERENCE_ROOT / "originals").glob("*.pdf"))
                    assert hashlib.sha256(Path(pdf_download.path()).read_bytes()).hexdigest() == hashlib.sha256(expected_pdf.read_bytes()).hexdigest()
                if not popup.is_closed():
                    popup.close()
                group.locator(".guide-button").first.click()
                see(page.locator("#guide-dialog-title")).to_contain_text("奖助学金管理办法")
                see(page.locator("#guide-content")).to_contain_text("SHA-256")
                save_screenshot(page, folder / f"usst-documents-{device}.png")
                page.locator("#guide-close").click()
                page.locator("#question").fill("审核后修改开题信息怎么办？")
                page.locator("#send").click()
                see(page.locator(".assistant[data-status=completed]")).to_have_count(2, timeout=15000)
                word = page.locator(".references").last
                word.locator("summary").click()
                see(word).to_contain_text("无可靠页码")
                see(word).to_contain_text("置未审核")
                with page.expect_download() as download_info:
                    word.locator(".original-button").first.click()
                downloaded = download_info.value
                path = downloaded.path()
                from app.reference_loader import REFERENCE_ROOT
                assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == hashlib.sha256((REFERENCE_ROOT / "originals" / downloaded.suggested_filename).read_bytes()).hexdigest()
                # 资料缺口交给当前模型说明，保留实际 mock/DeepSeek 模式。
                page.locator("#question").fill("中期考核最低多少学分？")
                page.locator("#send").click()
                see(page.locator(".assistant[data-status=completed]")).to_have_count(3, timeout=15000)
                see(page.locator(".assistant .message-label").last).to_contain_text("mock-campus-v1")
                see(page.locator(".assistant .message-body").last).to_contain_text("无法确认")
                see(page.locator("#mode")).to_have_text("模拟模型 · mock-campus-v1")
                page.reload()
                see(page.locator(".assistant[data-status=completed]")).to_have_count(3)
                assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
                assert not errors
                context.close()
                print(f"{device} 通过：文档摘录/表格、年份说明、PDF 原件、Word 下载、无依据提示及刷新恢复")
        finally:
            browser.close()


def main():
    results = []
    with isolated_database():
        command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
        from scripts.import_demo_guides import import_demo_guides
        from app.reference_loader import import_references
        from app.context_cache import get_context_cache
        from app.database import SessionLocal
        from app.models import Conversation
        from sqlalchemy import select
        assert import_demo_guides() == 8
        first = import_references()
        repeated = import_references()
        assert first["documents_added"] == 3 and first["chunks_added"] == 30
        assert repeated["documents_added"] == repeated["chunks_added"] == 0
        try:
            with running_server({"MODEL_PROVIDER": "mock", "MODEL_API_KEY": "", "MOCK_DELAY_SECONDS": "0", "MODEL_TIMEOUT_SECONDS": "30"}) as client:
                credentials = {"username": "reference_http_test", "password": secrets.token_urlsafe(24)}
                expect(client.post("/auth/register", json=credentials), 201)
                headers = {"Authorization": "Bearer " + expect(client.post("/auth/token", data=credentials), 200)["access_token"]}
                cid = expect(client.post("/conversations", headers=headers, json={}), 201)["id"]
                for question, domain, text, insufficient in CASES:
                    search = expect(client.get("/references/search", headers=headers, params={"q": question}), 200)
                    assert search["domain"] == domain and bool(search["insufficient_reason"]) == insufficient
                    assert text in "\n".join(ref["excerpt"] for ref in search["references"])
                    for ref in search["references"]:
                        assert ref["kind"] == "document" and not ref["is_demo"]
                        detail = expect(client.get(f"/references/chunks/{ref['id']}", headers=headers), 200)
                        assert detail["excerpt"] == ref["excerpt"] and detail["locator"] == ref["locator"]
                    with client.stream("POST", f"/conversations/{cid}/chat", headers=headers, json={"content": question}) as response:
                        events = list(event_stream(response))
                    assert events[-1][0] == "done"
                    if insufficient:
                        assert events[0][1]["mode"] == "mock"
                        answer = "".join(data["text"] for name, data in events if name == "delta")
                        assert "无法" in answer and "查询建议" in answer
                    results.append({"question": question, "domain": domain, "status": "passed", "insufficient_evidence": insufficient,
                                    "locators": [ref["locator"] for ref in search["references"]]})
                browser_flow(client, credentials)
        finally:
            with SessionLocal() as session:
                for conversation in session.scalars(select(Conversation)):
                    get_context_cache().delete(conversation.user_id, conversation.id)
    report = {"model_calls": "mock only; real calls = 0", "documents": 3, "chunks": 30, "usable_chunks": 29,
              "idempotent_import": True, "development_data_preserved": True, "cases": results}
    (PROJECT_ROOT / "docs" / "reference-acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print("10 个文档验收样例通过；测试库/明确缓存键已清理；真实模型调用 0 次")


if __name__ == "__main__":
    main()
