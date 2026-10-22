"""可重复导入演示指南；已有 demo_key 对应的记录保持不变。"""

import json

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.dialects.postgresql import insert

from app.config import PROJECT_ROOT
from app.database import SessionLocal, engine
from app.models import Guide

DEMO_FILE = PROJECT_ROOT / "data" / "demo_guides.json"


class DemoGuide(BaseModel):
    model_config = ConfigDict(extra="forbid")

    demo_key: str = Field(pattern=r"^demo-[a-z0-9-]+$", max_length=100)
    title: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=100)
    department: str = Field(min_length=1, max_length=200)
    steps: list[str] = Field(min_length=1)
    materials: list[str] = Field(min_length=1)
    source: str = Field(min_length=1)


def import_demo_guides() -> int:
    records = [DemoGuide.model_validate(item) for item in json.loads(DEMO_FILE.read_text(encoding="utf-8"))]
    keys = [record.demo_key for record in records]
    if len(keys) != len(set(keys)):
        raise ValueError("演示文件中的 demo_key 不能重复")
    if any("演示" not in record.source for record in records):
        raise ValueError("每条演示指南的来源说明都必须标注为演示数据")

    statement = (
        insert(Guide)
        .values([dict(record.model_dump(), is_demo=True) for record in records])
        .on_conflict_do_nothing(constraint="uq_guides_demo_key")
        .returning(Guide.id)
    )
    with SessionLocal.begin() as session:
        inserted_ids = session.scalars(statement).all()
    return len(inserted_ids)


def main() -> None:
    try:
        inserted = import_demo_guides()
        print(f"演示数据导入完成：新增 {inserted} 条；已有记录保留，不会重复导入。")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
