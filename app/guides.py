"""指南列表与详情查询接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cache import GuideCache, get_guide_cache
from app.database import get_session
from app.models import Guide
from app.schemas import ErrorResponse, GuidePage, GuideRead
from app.retrieval import keyword_filter

router = APIRouter(prefix="/guides", tags=["办事指南"])
SessionDep = Annotated[Session, Depends(get_session)]
CacheDep = Annotated[GuideCache, Depends(get_guide_cache)]


@router.get("", response_model=GuidePage, summary="筛选和分页查询指南")
def list_guides(
    session: SessionDep,
    keyword: Annotated[str | None, Query(max_length=100, description="搜索事项名称、办理部门及来源说明")] = None,
    category: Annotated[str | None, Query(max_length=100, description="按分类精确匹配")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 10,
) -> GuidePage:
    filters = []
    if keyword and (keyword := keyword.strip()):
        filters.append(keyword_filter(keyword))
    if category and (category := category.strip()):
        filters.append(Guide.category == category)

    total = session.scalar(select(func.count()).select_from(Guide).where(*filters)) or 0
    guides = session.scalars(
        select(Guide).where(*filters).order_by(Guide.id).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return GuidePage(
        items=[GuideRead.model_validate(guide) for guide in guides],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{guide_id}",
    response_model=GuideRead,
    responses={404: {"model": ErrorResponse, "description": "指南不存在"}},
    summary="查询指南详情（缓存 5 分钟）",
)
def get_guide(
    guide_id: Annotated[int, Path(ge=1, le=2_147_483_647)],
    session: SessionDep,
    cache: CacheDep,
) -> GuideRead:
    cached = cache.get(guide_id)
    if cached is not None:
        return cached
    guide = session.get(Guide, guide_id)
    if guide is None:
        raise HTTPException(status_code=404, detail="办事指南不存在")
    response = GuideRead.model_validate(guide)
    cache.set(response)
    return response
