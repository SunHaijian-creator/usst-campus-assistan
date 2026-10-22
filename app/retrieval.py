"""有界的字面关键词检索，不进行向量检索或语义理解。"""

from difflib import SequenceMatcher
import re

from sqlalchemy import or_, select

from app.models import Guide
from app.schemas import GuideRead


def is_short_followup(question):
    return re.fullmatch(
        r"(?:那|那么|还|请问|这个|这项)?(?:还)?(?:需要|要|有)?(?:什么|哪些)?"
        r"(?:材料|流程|办理流程|办理部门|部门|来源)(?:是什么|有哪些|呢)?[？?。！!]*",
        question.strip().replace(" ", ""),
    ) is not None


def keyword_filter(keyword, *, titles_only=False):
    if titles_only:
        return Guide.title.icontains(keyword, autoescape=True)
    return or_(
        Guide.title.icontains(keyword, autoescape=True),
        Guide.department.icontains(keyword, autoescape=True),
        Guide.source.icontains(keyword, autoescape=True),
    )


def retrieve_guides(session, question, limit=3):
    terms = []
    for word in re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9_]+", question[:200]):
        terms.extend(word[i:i + 2] for i in range(len(word) - 1))
    ignored = {"什么", "怎么", "需要", "如何", "办理", "可以", "请问", "一下", "哪些"}
    terms = list(dict.fromkeys(term for term in terms if term not in ignored))[:80]
    if not terms:
        return []
    # 聊天只在事项名称里匹配；避免每条来源里的“演示数据”等通用文字造成假命中。
    candidates = session.scalars(
        select(Guide).where(or_(*(keyword_filter(term, titles_only=True) for term in terms)))
        .order_by(Guide.id).limit(100)
    ).all()
    scored = sorted(
        ((SequenceMatcher(None, question, item.title, autojunk=False).find_longest_match().size, item) for item in candidates),
        key=lambda pair: (-pair[0], pair[1].id),
    )
    if not scored:
        return []
    threshold = max(2, scored[0][0] - 1)
    return [GuideRead.model_validate(item) for score, item in scored if score >= threshold][:limit]
