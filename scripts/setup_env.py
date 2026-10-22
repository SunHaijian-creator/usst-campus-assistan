"""仅首次生成本机配置；纯标准库，不打印密码，不覆盖已有 .env。"""

import os
from pathlib import Path
import secrets

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def setup_environment(root=PROJECT_ROOT):
    destination = root / ".env"
    if destination.exists():
        return False
    template = (root / ".env.example").read_text(encoding="utf-8")
    template = template.replace("POSTGRES_PASSWORD=replace_with_your_local_password", "POSTGRES_PASSWORD=" + secrets.token_urlsafe(32))
    template = template.replace("JWT_SECRET_KEY=\n", "JWT_SECRET_KEY=" + secrets.token_urlsafe(64) + "\n")
    # O_EXCL 避免检查后被其他进程创建时覆盖；0600 只允许当前用户读取。
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(template)
    return True


def main():
    if setup_environment():
        print("已生成本机 .env 与随机数据库密码/JWT 密钥，默认 mock；未显示任何凭据")
    else:
        print(".env 已存在，原密码、JWT 密钥与模型配置全部保留")


if __name__ == "__main__":
    main()
