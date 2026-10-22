"""面向三份小文档的确定性章节解析；不执行 DOC 宏、链接或文档内指令。"""

import hashlib
import json
from pathlib import Path
import re

from app.config import PROJECT_ROOT

REFERENCE_ROOT = PROJECT_ROOT / "data" / "references" / "usst"
AID_WARNING = "依据所提供的2024年文件，当前适用性尚未核验"
EXPECTED_KEYS = {"masters_financial_aid_2024", "midterm_assessment", "proposal_submission"}


def digest(content):
    return hashlib.sha256(content).hexdigest()


def normalize_excerpt(text):
    paragraphs = re.split(r"\n\s*\n", text.strip())
    return "\n\n".join(re.sub(r"\s*\n\s*", "", p).strip() for p in paragraphs if p.strip())


def chunk(key, heading, content, *, pages=None, kind="article", usable=True, table_rows=None):
    pages = pages or []
    location = heading + ("（PDF物理页 " + "、".join(map(str, pages)) + "）" if pages else "（Word栏目/注释，无可靠页码）")
    content = content.strip() if kind in {"table", "field", "unverified_diagram"} else normalize_excerpt(content)
    return {"chunk_key": key, "heading": heading, "locator": location, "pages": pages,
            "content": content, "content_sha256": digest(content.encode()), "kind": kind,
            "usable": usable, "table_rows": table_rows or []}


def financial_chunks(text):
    page_matches = list(re.finditer(r"^## PDF 第 (\d+) 页\s*\n(.*?)(?=^## |\Z)", text, re.M | re.S))
    if [int(m[1]) for m in page_matches] != list(range(1, 7)):
        raise ValueError("PDF 物理页标记缺失或重复")
    joined, offsets = "", []
    for match in page_matches:
        page, body = int(match[1]), match[2]
        body = re.sub(r"^\s*—\s*\d+\s*—\s*$", "", body, flags=re.M)
        body = re.sub(r"^\s*第[一二三四]章[^\n]*", "", body, flags=re.M).strip()
        start = len(joined)
        joined += body + "\n"
        offsets.append((page, start, len(joined)))
    markers = list(re.finditer(r"第([一二三四五六七八九十]+)条", joined))
    numerals = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二", "十三"]
    if [m[1] for m in markers] != numerals:
        raise ValueError("奖助文件条款边界与预期不一致，需人工核对")
    output = []
    for number, marker in enumerate(markers, 1):
        end = markers[number].start() if number < len(markers) else len(joined)
        body = joined[marker.start():end]
        pages = [page for page, a, b in offsets if a < end and b > marker.start()]
        # 第十三条之后的办公室印发落款不属于条款。
        if number == 13:
            body = body.split("校长办公室")[0]
        chapter = "第一章" if number <= 2 else "第二章" if number <= 6 else "第三章" if number <= 11 else "第四章"
        if number != 2:
            output.append(chunk(f"article-{number}", f"{chapter} 第{numerals[number - 1]}条", body, pages=pages))
            continue
        sections = list(re.finditer(r"（([一二三四])）", body))
        if [m[1] for m in sections] != ["一", "二", "三", "四"]:
            raise ValueError("奖助标准分项不完整")
        for index, section in enumerate(sections):
            part_end = sections[index + 1].start() if index + 1 < len(sections) else len(body)
            content = body[section.start():part_end]
            global_start, global_end = marker.start() + section.start(), marker.start() + part_end
            section_pages = [p for p, a, b in offsets if a < global_end and b > global_start]
            if index == 1:
                # 扁平表格的合并单元格不能按文本相邻关系配对；独立导入已核对转录表。
                content = content.split("全日制硕士研究生学业奖学金资助标准")[0]
            output.append(chunk(f"standard-{index + 1}", f"第一章 第二条（{section[1]}）", content, pages=section_pages))
    table_text = text.split("## 第3页奖学金表格的结构化转录", 1)[1]
    lines = [line.strip() for line in table_text.splitlines() if line.startswith("|")]
    rows = []
    for line in lines[2:]:
        stage, level, ratio, amount = [part.strip() for part in line.strip("|").split("|")]
        rows.append({"stage": stage, "level": level, "ratio": ratio, "amount_yuan_per_year_per_student": int(amount)})
    expected = [("第一阶段", "一等", "20%", 5000), ("第一阶段", "二等", "80%", 3000),
                ("第二阶段", "一等", "5%", 12000), ("第二阶段", "二等", "35%", 8000), ("第二阶段", "三等", "50%", 4000)]
    if [tuple(row.values()) for row in rows] != expected:
        raise ValueError("奖学金转录表对应关系变化，需回看 PDF 人工核对")
    output.append(chunk("academic-table", "第一章 第二条（二）学业奖学金资助标准表（结构化转录）",
                        "\n".join(lines), pages=[3], kind="table", table_rows=rows))
    return output


def numbered_notes(text, prefix):
    markers = list(re.finditer(r"(?:注：)?\s*([1-4])、", text))
    if [m[1] for m in markers] != ["1", "2", "3", "4"]:
        raise ValueError("Word 正文注释编号不完整")
    return [chunk(f"note-{m[1]}", f"{prefix} 注释{m[1]}",
                  text[m.start():markers[i + 1].start() if i + 1 < len(markers) else len(text)], kind="note")
            for i, m in enumerate(markers)]


def midterm_chunks(text):
    body = text.split("下文竖线表示原表格单元格边界。", 1)[1].strip()
    notes_at = body.index("注：1、")
    form, notes = body[:notes_at], body[notes_at:]
    headings = list(re.finditer(r"一、学分审核|二、科研及论文情况|三、考核评定", form))
    if len(headings) != 3:
        raise ValueError("中期表格栏目缺失")
    output = [chunk("header", "表头：姓名、学号、导师、专业、开题时间与题目", form[:headings[0].start()], kind="field")]
    for i, heading in enumerate(headings):
        content = form[heading.start():headings[i + 1].start() if i + 1 < len(headings) else len(form)]
        output.append(chunk(f"field-{i + 1}", heading[0], content, kind="field"))
    return output + numbered_notes(notes, "中期考核表")


def proposal_chunks(text):
    body = text.split("## 正文及注释", 1)[1].split("## 流程图文本框标签", 1)[0]
    output = numbered_notes(body[body.index("注：1、"):], "开题信息提交")
    diagram = text.split("## 流程图文本框标签", 1)[1]
    output.append(chunk("unverified-diagram", "流程图文本框标签（箭头关系未核验，禁止用于生成步骤）",
                        diagram, kind="unverified_diagram", usable=False))
    return output


def load_bundle(root=REFERENCE_ROOT):
    root = Path(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8-sig"))
    if len(manifest["documents"]) != 3 or {doc["id"] for doc in manifest["documents"]} != EXPECTED_KEYS:
        raise ValueError("本导入器仅接受约定的三份资料")
    result = []
    builders = {"masters_financial_aid_2024": financial_chunks, "midterm_assessment": midterm_chunks,
                "proposal_submission": proposal_chunks}
    for document in manifest["documents"]:
        paths = [root / document[key] for key in ("original_path", "text_path")]
        if any(not path.resolve().is_relative_to(root.resolve()) or path.is_symlink() for path in paths):
            raise ValueError("资料路径越界或包含符号链接")
        original, extracted = (path.read_bytes() for path in paths)
        if digest(original) != document["sha256"] or paths[0].name != document["source_filename"]:
            raise ValueError("原件哈希或文件名与清单不一致")
        warnings = ["用户提供的本地资料，未核验官方来源和当前有效性", *document.get("warnings", [])]
        if document["id"] == "masters_financial_aid_2024":
            warnings.append(AID_WARNING)
        spec = {"document_key": document["id"], "title": document["title"],
                "original_filename": document["source_filename"], "file_sha256": document["sha256"],
                "text_sha256": digest(extracted), "version": document.get("document_number", "原文未注明版本"),
                "provenance": "用户提供的本地文档", "currency_verified": False, "warnings": warnings,
                "document_meta": {k: document[k] for k in ("issue_date", "effective_date", "page_count") if k in document},
                "active": True}
        result.append((spec, builders[document["id"]](extracted.decode("utf-8-sig").replace("\r\n", "\n"))))
    return result


def import_references(root=REFERENCE_ROOT):
    from sqlalchemy import select, text
    from app.database import SessionLocal
    from app.models import ReferenceChunk, ReferenceDocument

    prepared = load_bundle(root)
    added_docs, added_chunks = 0, 0
    with SessionLocal.begin() as session:
        # 同一资料集并行导入也必须幂等；只锁本项目的导入事务。
        session.execute(text("SELECT pg_advisory_xact_lock(827104335)"))
        for spec, chunks in prepared:
            for old in session.scalars(select(ReferenceDocument).where(
                ReferenceDocument.document_key == spec["document_key"], ReferenceDocument.file_sha256 != spec["file_sha256"]
            )):
                old.active = False
            document = session.scalar(select(ReferenceDocument).where(
                ReferenceDocument.document_key == spec["document_key"], ReferenceDocument.file_sha256 == spec["file_sha256"]))
            if document is None:
                document = ReferenceDocument(**spec)
                session.add(document); session.flush(); added_docs += 1
            elif document.text_sha256 != spec["text_sha256"]:
                raise ValueError("同一原件的提取文本已改变，请人工审查后建立新提取版本")
            else:
                document.active = True
            existing = {item.chunk_key: item for item in session.scalars(select(ReferenceChunk).where(ReferenceChunk.document_id == document.id))}
            for item in chunks:
                if item["chunk_key"] not in existing:
                    session.add(ReferenceChunk(document_id=document.id, **item)); added_chunks += 1
                elif existing[item["chunk_key"]].content_sha256 != item["content_sha256"]:
                    raise ValueError("已导入片段发生变化，请人工审查，不自动覆盖引用定位")
    return {"documents_added": added_docs, "chunks_added": added_chunks,
            "documents_in_bundle": len(prepared), "chunks_in_bundle": sum(len(items) for _, items in prepared)}
