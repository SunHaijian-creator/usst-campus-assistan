"""暂存用户资料包并幂等导入 PostgreSQL；不请求模型或嵌入服务。"""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

from app.config import PROJECT_ROOT

REFERENCE_ROOT = PROJECT_ROOT / "data" / "references" / "usst"


def stage_bundle(archive, destination=REFERENCE_ROOT):
    destination = Path(destination)
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        names = ["README.md", "manifest.json"]
        for document in manifest["documents"]:
            names.extend([document["original_path"], document["text_path"]])
            if hashlib.sha256(bundle.read(document["original_path"])).hexdigest() != document["sha256"]:
                raise ValueError("原件哈希与资料清单不一致")
        if len(names) != len(set(names)):
            raise ValueError("资料路径重复")
        planned = []
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise ValueError("资料包包含不安全路径")
            target = destination / name
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError("资料路径越界")
            info = bundle.getinfo(name)
            if info.file_size > 5_000_000 or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("资料文件大小或类型不受支持")
            content = bundle.read(name)
            if target.exists() and target.read_bytes() != content:
                raise ValueError("目标文件已存在且内容不同；请人工核对，不自动覆盖")
            planned.append((target, content))
        for target, content in planned:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_bytes(content)
    return len(planned)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="首次暂存的用户 ZIP 路径")
    parser.add_argument("--stage-only", action="store_true", help="只校验并复制文件，不访问数据库")
    args = parser.parse_args()
    if args.archive:
        print(f"资料包已校验并暂存：{stage_bundle(args.archive)} 个文件（已有同内容文件保留）")
    if not args.stage_only:
        from app.reference_loader import import_references
        print(json.dumps(import_references(), ensure_ascii=False))


if __name__ == "__main__":
    main()
