"""三份资料的中文主题路由、词面排序与必要条款联检；无向量/付费服务。"""

from dataclasses import dataclass, field
import math
import re

from sqlalchemy import select

from app.models import ReferenceChunk, ReferenceDocument
from app.reference_loader import AID_WARNING
from app.schemas import DocumentReference

AID = "masters_financial_aid_2024"
MIDTERM = "midterm_assessment"
PROPOSAL = "proposal_submission"


def compact(text):
    return re.sub(r"\s+", "", text)


def domain_for(question):
    direction_change = (
        any(word in question for word in ("研究方向", "论文方向", "论文内容"))
        and any(word in question for word in ("修改", "变更", "调整", "重新开题"))
    )
    if direction_change or any(word in question for word in ("中期", "考核表", "最低学分")):
        return MIDTERM
    if any(word in question for word in ("奖学金", "助学金", "奖助", "三助", "助研", "助教", "助管")):
        return AID
    if any(word in question for word in ("开题", "置未审核", "审核后修改", "审核通过后修改", "学院审核")):
        return PROPOSAL
    return None


def is_document_followup(question, previous_domain=None):
    if re.fullmatch(r"(?:那|那么|这个|还)?(?:金额|比例|资助比例|适用对象|例外|发放时间|申请条件|生效时间)(?:是多少|是什么|有哪些|呢)?[？?。]*", compact(question)):
        return True
    # 省略“开题”的操作追问仅承接开题主题，不能将普通审核问题直接归入校内流程。
    return previous_domain == PROPOSAL and any(
        word in question for word in ("审核通过", "审核后", "未审核", "填错", "修改信息", "怎么修改")
    )


def lexical_score(question, text):
    """有界中文双字词重合，长短查询都可工作；主题/章节精确词另外加权。"""
    question, text = compact(question[:500]), compact(text)
    terms = set(question[i:i + 2] for i in range(len(question) - 1)) - {"什么", "怎么", "多少", "如何", "哪些", "请问", "需要"}
    return sum(1 + math.log1p(text.count(term)) for term in terms if term in text)


@dataclass
class DocumentSearch:
    domain: str | None = None
    references: list[DocumentReference] = field(default_factory=list)
    insufficient_reason: str | None = None


def citation(chunk, document, number):
    return DocumentReference(citation_id=f"R{number}", id=chunk.id, document_id=document.id,
        title=document.title, original_filename=document.original_filename, file_sha256=document.file_sha256,
        version=document.version, source=document.provenance, locator=chunk.locator, pages=chunk.pages,
        excerpt=chunk.content, warnings=document.warnings, table_rows=chunk.table_rows,
        original_url=f"/references/documents/{document.id}/original")


def search_documents(session, question, *, context_domain=None):
    domain = domain_for(question) or context_domain
    result = DocumentSearch(domain=domain)
    if domain is None:
        return result
    pairs = session.execute(select(ReferenceChunk, ReferenceDocument).join(ReferenceDocument).where(
        ReferenceDocument.document_key == domain, ReferenceDocument.active.is_(True), ReferenceChunk.usable.is_(True)
    ).order_by(ReferenceChunk.id)).all()
    if not pairs:
        result.insufficient_reason = "未检索到这类问题的已导入资料，无法确认相关要求，请向学校相关部门核实。"
        return result
    by_key = {item.chunk_key: (item, document) for item, document in pairs}
    chosen = []

    def include(*keys):
        for key in keys:
            if key in by_key and key not in chosen:
                chosen.append(key)

    if domain == AID:
        topical = []
        for words, keys in [(("公示",), ("article-5",)), (("申诉",), ("article-6",)),
                            (("监督",), ("article-11",)), (("生效", "实施时间"), ("article-12",)),
                            (("谁解释", "负责解释"), ("article-13",))]:
            if any(word in question for word in words):
                topical.extend(keys)
        include(*topical)
        want_standards = not topical or any(word in question for word in ("金额", "比例", "多少钱", "标准"))
        matched_type = False
        if "国家奖学金" in question:
            if want_standards: include("standard-1")
            matched_type = True
        if "国家助学金" in question or ("助学金" in question and "奖助" not in question):
            if want_standards: include("standard-3")
            matched_type = True
        if any(word in question for word in ("学业", "等级", "比例", "一等", "二等", "三等")):
            if want_standards: include("standard-2", "academic-table")
            matched_type = True
        if any(word in question for word in ("三助", "助研", "助教", "助管")):
            if want_standards: include("standard-4")
            matched_type = True
        if not matched_type and not topical:
            ranked = sorted(pairs, key=lambda pair: -lexical_score(question, pair[0].content + pair[0].heading))
            for item, _ in ranked[:2]:
                include(item.chunk_key)
            if not any(word in question for word in ("公示", "申诉", "监督", "实施", "解释", "发放", "休学", "处分", "注册")):
                include("standard-1", "standard-2", "academic-table", "standard-3", "standard-4")
        if any(word in question for word in ("发放", "每月", "按月", "什么时候发")):
            include("article-7")
        if "公示" in question:
            include("article-5")
        if "申诉" in question:
            include("article-6")
        if "监督" in question:
            include("article-11")
        # 金额、比例始终与适用对象、排除对象及管理例外一起进入模型上下文。
        include("article-1", "article-8", "article-9", "article-10")
        if any(word in question for word in ("最新", "现行", "今年", "当前", "现在", "目前", "2025", "2026", "2027", "博士", "本科", "专科")):
            result.insufficient_reason = AID_WARNING + "。本资料仅记载全日制硕士研究生的相关规定，无法确认所问年份或其他适用对象的标准。"
        elif any(word in question for word in ("申请截止", "提交截止", "最低绩点", "挂科", "剩余10", "剩余 10", "为什么90", "为什么 90")):
            result.insufficient_reason = AID_WARNING + "。所提供条款没有明确所问的具体要求，无法据此推断。"
        elif "助学金" in question and any(word in question for word in ("每月多少", "月发多少", "每个月多少")):
            include("article-7")
            result.insufficient_reason = AID_WARNING + "。原文记载每生每年6000元、按月划拨，未写每月具体发放金额，无法确认月实发数额，不能直接按12个月推算。"
    elif domain == MIDTERM:
        if any(word in question for word in ("学分", "截止", "时间", "日期")):
            include("header", "field-1")
        elif any(word in question for word in ("方向", "修改", "重新开题")):
            include("note-3")
        elif any(word in question for word in ("小组", "成员", "职称", "几人", "多少人")):
            include("note-1", "field-3")
        elif any(word in question for word in ("打印", "保存", "几份", "A4")):
            include("note-4")
        else:
            ranked = sorted(pairs, key=lambda pair: -lexical_score(question, pair[0].heading + pair[0].content))
            for item, _ in ranked[:3]:
                include(item.chunk_key)
        if any(word in question for word in ("最低", "至少多少学分", "多少学分", "截止", "什么时候", "哪天", "几月", "日期要求")):
            result.insufficient_reason = "所提供《中期考核表》是空白模板，仅有已修学分、开题时间或签字日期等待填栏目；未写明截止时间、最低学分，无法确认这些具体要求。请向学院核实。"
    else:
        if any(word in question for word in ("审核后", "审核通过", "审核完", "置未审核", "发现错误")):
            include("note-3", "note-4")
        elif any(word in question for word in ("修改", "未审核", "审核前")):
            include("note-1", "note-3", "note-4")
        elif "审核" in question or "提交" in question:
            include("note-1", "note-2", "note-3", "note-4")
        else:
            include("note-1", "note-2", "note-3", "note-4")
        specific_note = any(word in question for word in ("修改", "置未审核", "学院审核", "未审核", "审核通过", "审核后", "填错", "发现错误"))
        process_question = any(word in question for word in ("流程", "步骤", "顺序", "怎么走", "如何办理", "怎么开题", "如何开题", "怎么办", "怎么进行", "如何进行"))
        if any(word in question for word in ("截止", "最晚", "哪天", "几月", "什么时候", "日期")):
            result.insufficient_reason = "所提供开题资料正文注释未记载提交截止日期，无法确认本年度最晚提交时间；需查询所在学院当年通知或向教学秘书确认，不能从流程图排列推算日期。"
        elif any(word in question for word in ("网址", "材料")) or (process_question and (not specific_note or "完整" in question or "全部" in question)):
            result.insufficient_reason = "开题文件的流程图箭头关系尚未核对，文本框提取排列不是办理顺序，无法据此生成完整流程；所提供正文注释也不足以确认所问的其他具体要求。当前仅可引用已提取的四条正文注释。"
    result.references = [citation(*by_key[key], number) for number, key in enumerate(chosen, 1)]
    return result
