"""流式校验文档引用；未检索编号不得出现在输出或成为可点击来源。"""

import re

from app.model_gateway import ModelFailure


async def validate_citations(chunks, allowed, *, reject_numeric=True, used=None):
    pending = ""
    citation_start = r"[\[【]\s*[A-Za-z]?\d" if reject_numeric else r"[\[【]\s*[RrGg]\d"
    async for part in chunks:
        pending += part
        while pending:
            match = re.search(r"[\[【]", pending)
            if match is None:
                yield pending; pending = ""; break
            if match.start():
                yield pending[:match.start()]; pending = pending[match.start():]
            end_char = "]" if pending[0] == "[" else "】"
            end = pending.find(end_char, 1)
            if end < 0:
                if len(pending) <= 128:
                    break  # 引用编号可以跨任意网络块，先不输出未完成的引用。
                if re.match(citation_start, pending):
                    raise invalid_citation()
                yield pending[0]; pending = pending[1:]; continue
            token = pending[1:end].strip()
            ids = re.findall(r"[A-Za-z]\d+", token)
            if not reject_numeric:
                # 空检索的一般分析允许普通数组/数学记法 [x1, x2]、[1, 2]。
                # 仍拦截冒充本项目来源的 R/G 编号。
                ids = [item for item in ids if item[0] in "RrGg"]
            if ids and (any(item not in allowed for item in ids) or re.sub(r"[A-Za-z]\d+|[,，、\s]", "", token)):
                raise invalid_citation()
            if re.fullmatch(r"[\d\s,，、;；\-]+", token) and re.search(r"\d", token):
                if token not in allowed and reject_numeric:
                    raise invalid_citation()
                if token in allowed:
                    ids.append(token)
            if used is not None:
                used.update(ids)
            yield pending[:end + 1]; pending = pending[end + 1:]
    if pending:
        if re.match(citation_start, pending):
            raise invalid_citation()
        yield pending


def invalid_citation():
    return ModelFailure("回复包含无法核验的引用编号，未作为完整答案保存，请核对参考资料后重试", code="invalid_citation")
