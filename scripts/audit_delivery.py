"""不回显秘密的候选文件/Git元数据检查；不提交、不推送、不读取数据库内容。"""

import fnmatch
import json
from pathlib import Path
import re
import subprocess

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {".git", ".agents", ".codex", ".aws", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "node_modules"}
SENSITIVE_PATTERNS = (".env", ".env.*", "*.dump", "*.backup", "*.sql", "*.sqlite*", "*.db", "*.pem", "*.key", "*.log")


def secret_values():
    values = dotenv_values(PROJECT_ROOT / ".env")
    return [values[name].encode() for name in ("MODEL_API_KEY", "POSTGRES_PASSWORD", "JWT_SECRET_KEY", "REDIS_PASSWORD")
            if values.get(name) and len(values[name]) >= 8]


def candidates():
    tracked = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                             cwd=PROJECT_ROOT, capture_output=True)
    if tracked.returncode == 0:
        paths = [Path(name.decode()) for name in tracked.stdout.split(b"\0") if name]
        status = subprocess.run(["git", "status", "--porcelain", "-z"], cwd=PROJECT_ROOT, capture_output=True)
        return paths, {"available": True, "pending_entries": len([p for p in status.stdout.split(b"\0") if p])}
    paths = []
    import os
    for current, directories, files in os.walk(PROJECT_ROOT):
        directories[:] = [d for d in directories if d not in EXCLUDED_DIRS]
        for name in files:
            relative = (Path(current) / name).relative_to(PROJECT_ROOT)
            if name != ".env.example" and any(fnmatch.fnmatch(name, pattern) for pattern in SENSITIVE_PATTERNS):
                continue
            paths.append(relative)
    return paths, {"available": False, "reason": "当前目录不是有效Git仓库；仅检查交付候选文件，未初始化或修改Git"}


def main():
    paths, git = candidates()
    known = secret_values()
    issues = []
    for relative in paths:
        path = PROJECT_ROOT / relative
        if not path.is_file():
            continue
        if relative.name != ".env.example" and (any(p in EXCLUDED_DIRS for p in relative.parts)
                or any(fnmatch.fnmatch(relative.name, pattern) for pattern in SENSITIVE_PATTERNS)):
            issues.append({"path": str(relative), "reason": "敏感文件或本机环境不应跟踪"})
            continue
        content = path.read_bytes()
        if any(value in content for value in known):
            issues.append({"path": str(relative), "reason": "含本机真实秘密值（未回显）"})
        for pattern, label in [
            (rb"\bsk-[A-Za-z0-9_-]{20,}\b", "疑似API密钥"),
            (rb"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b", "疑似可用JWT"),
            (rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", "私钥"),
        ]:
            if re.search(pattern, content):
                issues.append({"path": str(relative), "reason": label + "（未回显）"})
        if relative.suffix == ".json" and relative.parts[0] == "docs":
            def walk(value):
                if isinstance(value, dict):
                    for key, child in value.items():
                        if key.lower() in {"password", "hashed_password", "access_token", "refresh_token", "authorization", "username", "model_api_key", "jwt_secret_key"}:
                            issues.append({"path": str(relative), "reason": "验收记录不应保存账号或凭据字段"})
                        walk(child)
                elif isinstance(value, list):
                    for child in value:
                        walk(child)
            walk(json.loads(content))
    report = {"git": git, "candidate_files_checked": len(paths), "known_secret_values_checked": len(known),
              "issues": issues, "no_commit_or_push": True,
              "scope": "候选文件与验收JSON；截图显示名由浏览器截图工具匿名；不是历史提交的秘密审计"}
    (PROJECT_ROOT / "docs" / "delivery-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if issues:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
