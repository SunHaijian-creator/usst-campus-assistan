"""手动真实 API 验收：最多三次短请求，无重试；不会由 pytest 自动执行。"""

from datetime import datetime, timezone
import json
import logging
import secrets
import time

import httpx
from alembic import command
from alembic.config import Config

from app.config import PROJECT_ROOT, get_settings
from app.deepseek import configuration_error
from scripts.test_database import isolated_database
from scripts.verify_auth_http import expect, running_server
from scripts.verify_chat_http import event_stream

REPORT = PROJECT_ROOT / "docs" / "deepseek-live-result.json"
MAX_REQUESTS = 3


def main():
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = get_settings()
    if (settings.model_provider != "deepseek" or configuration_error(settings)
            or settings.model_base_url.rstrip("/") != "https://api.deepseek.com"):
        print("未发送请求：请先在本机配置 DeepSeek 密钥、模式及官方 Base URL。")
        return
    if REPORT.exists() and json.loads(REPORT.read_text()).get("requests_started", 0):
        print("已有真实验收记录，不自动重复请求；请先查看 docs/deepseek-live-result.json。")
        return
    # 只在内存与临时子进程环境中传递，不写进命令行、报告或日志。
    private_values = [settings.model_api_key.get_secret_value(), settings.postgres_password.get_secret_value(),
                      settings.jwt_secret_key.get_secret_value()]
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "provider": "deepseek",
              "model": settings.model_name, "max_tokens_per_request": min(settings.model_max_tokens, 256),
              "max_requests": MAX_REQUESTS, "requests_started": 0, "automatic_retries": 0, "results": []}

    def save():
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    save()
    try:
        with isolated_database():
            command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
            from scripts.import_demo_guides import import_demo_guides
            from app.context_cache import get_context_cache
            from app.database import SessionLocal
            from app.models import Conversation
            from sqlalchemy import select
            assert import_demo_guides() == 8
            options = {"MODEL_PROVIDER": "deepseek", "MODEL_API_KEY": settings.model_api_key.get_secret_value(),
                       "MODEL_BASE_URL": settings.model_base_url, "MODEL_NAME": settings.model_name,
                       "MODEL_MAX_TOKENS": str(report["max_tokens_per_request"]),
                       "MODEL_TIMEOUT_SECONDS": str(settings.model_timeout_seconds)}
            try:
                with running_server(options) as client:
                    client.timeout = httpx.Timeout(settings.model_timeout_seconds + 10)
                    credentials = {"username": "deepseek_live_acceptance", "password": secrets.token_urlsafe(24)}
                    expect(client.post("/auth/register", json=credentials), 201)
                    access_token = expect(client.post("/auth/token", data=credentials), 200)["access_token"]
                    private_values.extend([credentials["password"], access_token])
                    headers = {"Authorization": "Bearer " + access_token}
                    cid = expect(client.post("/conversations", headers=headers, json={"title": "真实模型短请求验收"}), 201)["id"]
                    url = f"/conversations/{cid}"
                    purposes = [("连接", "请只回复：连接成功"),
                                ("指南回答", "校园卡补办需要什么材料？请用两句话回答。"),
                                ("多轮追问", "办理流程呢？")]
                    for number, (purpose, question) in enumerate(purposes, 1):
                        assert report["requests_started"] < MAX_REQUESTS
                        report["requests_started"] += 1
                        result = {"number": number, "purpose": purpose, "question": question}
                        report["results"].append(result)
                        save()  # 先记录预算消耗，异常或重启也不自动追加付费请求。
                        started = time.monotonic()
                        with client.stream("POST", url + "/chat", headers=headers, json={"content": question}) as response:
                            result["http_status"] = response.status_code
                            if response.status_code != 200:
                                result["status"] = "http_error"
                                save()
                                break
                            events = []
                            for name, data in event_stream(response):
                                if name == "delta" and "first_delta_seconds" not in result:
                                    result["first_delta_seconds"] = round(time.monotonic() - started, 3)
                                events.append((name, data))
                        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
                        meta = next((data for name, data in events if name == "meta"), {})
                        error = next((data for name, data in events if name == "error"), None)
                        answer = "".join(data["text"] for name, data in events if name == "delta")
                        # 即使发生意外回显，也绝不写出真实凭据或回答正文。
                        if any(value and value in answer for value in private_values):
                            result["status"] = "credential_echo_blocked"
                            save()
                            break
                        result.update({"mode": meta.get("mode"), "model": meta.get("model"),
                                       "context_rounds": meta.get("context_rounds"), "references": meta.get("references", []),
                                       "retrieval_source": meta.get("retrieval_source"),
                                       "delta_events": sum(name == "delta" for name, _ in events), "answer": answer})
                        if error:
                            result.update(status="error", error_code=error.get("code"))
                            save()
                            print(f"第 {number} 次（{purpose}）：失败，安全错误码 {error.get('code')}；未重试。", flush=True)
                            break
                        rows = expect(client.get(url + "/messages", headers=headers), 200)["items"]
                        checks = {"normal_end": bool(events and events[-1][0] == "done"),
                                  "real_provider": meta.get("mode") == "deepseek" and meta.get("simulated") is False,
                                  "history_saved": rows[-1]["status"] == "completed" and rows[-1]["content"] == answer,
                                  "context_rounds": meta.get("context_rounds") == number - 1}
                        if number == 1:
                            checks["connection_reply"] = "连接成功" in answer
                        else:
                            checks["demo_label"] = "演示" in answer
                            checks["reference_guide"] = bool(result["references"]) and all(
                                ref["title"] == "校园卡补办" and ref["is_demo"] for ref in result["references"])
                            if number == 2:
                                checks["materials_grounded"] = "身份证" in answer and ("学生证" in answer or "学号" in answer)
                            else:
                                checks["followup_retrieval"] = meta.get("retrieval_source") == "last_completed_question"
                                checks["steps_grounded"] = sum(term in answer for term in ("挂失", "申请", "领取")) >= 2
                        result.update(checks=checks, status="passed" if all(checks.values()) else "needs_review")
                        save()
                        print(f"第 {number} 次（{purpose}）：{result['status']}，{result['delta_events']} 个增量，{result['elapsed_seconds']} 秒。", flush=True)
                        if result["status"] != "passed":
                            break
            finally:
                with SessionLocal() as session:
                    for conversation in session.scalars(select(Conversation)):
                        get_context_cache().delete(conversation.user_id, conversation.id)
            report["isolated_data_cleaned"] = True
        report["test_database_removed"] = True
    except Exception as error:
        # 只记类型，不输出潜在带凭据的连接异常或原始响应。
        report["runner_error_type"] = type(error).__name__
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        save()
        print(f"本次聊天请求尝试 {report['requests_started']} 次，上限 3 次，自动重试 0 次；报告已保存。", flush=True)


if __name__ == "__main__":
    main()
