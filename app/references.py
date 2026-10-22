"""只接受数据库 ID 的受保护资料接口；不接受客户端文件路径。"""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_session
from app.models import ReferenceChunk, ReferenceDocument
from app.reference_loader import REFERENCE_ROOT, digest
from app.reference_search import citation, search_documents
from app.security import CurrentUser

router = APIRouter(prefix="/references", tags=["用户提供的参考资料"])
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/search", summary="检索已导入文档原文，不调用模型")
def search(q: Annotated[str, Query(min_length=1, max_length=500)], user: CurrentUser, session: SessionDep):
    result = search_documents(session, q)
    return {"domain": result.domain, "references": result.references, "insufficient_reason": result.insufficient_reason}


@router.get("/chunks/{chunk_id}", summary="查看引用片段与定位")
def detail(chunk_id: int, user: CurrentUser, session: SessionDep):
    pair = session.execute(select(ReferenceChunk, ReferenceDocument).join(ReferenceDocument).where(
        ReferenceChunk.id == chunk_id, ReferenceChunk.usable.is_(True)
    )).first()
    if pair is None:
        raise HTTPException(404, "参考片段不存在")
    data = citation(*pair, 1).model_dump()
    data.pop("citation_id")  # 编号属于某次检索/回答，不是片段的永久 ID。
    return data


def checked_original(document):
    filename = document.original_filename
    root = REFERENCE_ROOT / "originals"
    if Path(filename).name != filename or "\\" in filename or Path(filename).suffix.lower() not in {".pdf", ".doc"}:
        raise HTTPException(404, "资料原件不存在")
    file = root / filename
    if (root.is_symlink() or file.is_symlink() or not file.resolve().is_relative_to(root.resolve())
            or not file.is_file() or digest(file.read_bytes()) != document.file_sha256):
        raise HTTPException(404, "资料原件不存在或校验失败")
    return file


@router.get("/documents/{document_id}/original", summary="获取已登记的原件，须登录")
def original(document_id: int, user: CurrentUser, session: SessionDep):
    document = session.get(ReferenceDocument, document_id)
    if document is None:
        raise HTTPException(404, "资料原件不存在")
    file = checked_original(document)
    media = "application/pdf" if file.suffix.lower() == ".pdf" else "application/msword"
    return FileResponse(file, media_type=media, filename=document.original_filename,
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
