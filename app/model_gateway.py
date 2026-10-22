"""统一异步分段接口；模拟模型和 DeepSeek 共享聊天状态机。"""

import asyncio
from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol

from fastapi import HTTPException

from app.config import get_settings
from app.schemas import DocumentReference, GuideRead


@dataclass
class ModelInput:
    question: str
    turns: list[dict]
    guides: list[GuideRead]
    scenario: str = "normal"
    documents: list[DocumentReference] = field(default_factory=list)
    insufficient_reason: str | None = None


class ModelProvider(Protocol):
    def stream(self, data: ModelInput) -> AsyncIterator[str]: ...


class ModelFailure(Exception):
    def __init__(self, message="回复生成失败，请稍后重试", *, code="generation_failed"):
        super().__init__(message)
        self.code = code
        self.safe_message = message


class MockProvider:
    mode = "mock"
    model_name = "mock-campus-v1"

    async def stream(self, data: ModelInput):
        settings = get_settings()
        text = "【模拟回复 · 未接入真实模型】\n"
        text += f"本轮接收到的问题：{data.question[:160]}\n"
        text += f"短期上下文包含 {len(data.turns)} 轮已完成问答。这里使用固定模板，不代表语义理解。\n"
        if data.turns:
            text += f"上一轮的问题记录：{data.turns[-1]['user'][:100]}\n"
        if data.documents:
            text += "以下为实际检索的用户提供资料原文，不是虚构指南；此处只演示摘录，不进行语义推理。\n"
            for reference in data.documents:
                text += f"\n[{reference.citation_id}] {reference.title} · {reference.locator}\n"
                text += "\n".join(reference.warnings) + "\n原文摘录：" + reference.excerpt + "\n"
        elif data.guides:
            text += "以下只是按字面关键词匹配的资料，不构成真实校园政策解释。\n"
            for guide in data.guides:
                label = "演示数据（虚构）" if guide.is_demo else "来源资料，需自行核实"
                text += f"\n[{guide.id}] {guide.title} · {label}\n"
                text += "所需材料：" + "、".join(guide.materials) + "\n"
                text += "办理流程：" + "；".join(guide.steps) + "\n"
                text += "来源说明：" + guide.source + "\n"
        else:
            text += "本次未检索到相关资料。真实模型仍可进行一般分析与交流；缺乏依据的具体校内规定无法确认，不能编造。此模拟模板不进行语义分析。\n"
        if data.insufficient_reason:
            text += "\n具体资料缺口：" + data.insufficient_reason + "\n"
            text += "查询建议：向所在学院教学秘书或研究生管理部门确认最新通知、适用年级及提交要求；以下一般建议应与文档规定区分。\n"
        text += "\n本次仅演示多轮记录、资料引用与流式显示，请勿将模拟回复作为办事依据。"
        for index, start in enumerate(range(0, len(text), settings.mock_chunk_chars)):
            if data.scenario == "failure" and index >= settings.mock_failure_after_chunks:
                raise ModelFailure("模拟回复生成失败，请重试")
            await asyncio.sleep(settings.mock_delay_seconds)
            yield text[start:start + settings.mock_chunk_chars]
            if data.scenario == "timeout":
                await asyncio.sleep(settings.model_timeout_seconds + 10)
        if data.scenario == "failure":
            # 即使整段文本比设置的分段数少，失败演示也不能误报 done。
            raise ModelFailure("模拟回复生成失败，请重试")


def get_model_provider() -> ModelProvider:
    settings = get_settings()
    if settings.model_provider == "mock":
        return MockProvider()
    if settings.model_provider == "deepseek":
        from app.deepseek import DeepSeekProvider, configuration_error
        if reason := configuration_error(settings):
            raise HTTPException(status_code=503, detail=reason)
        return DeepSeekProvider(settings)
    raise HTTPException(status_code=503, detail="模型供应商尚未接入，请配置 mock 或 deepseek")
