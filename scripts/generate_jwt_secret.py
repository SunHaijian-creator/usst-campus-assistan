"""只在本机 .env 缺少密钥时生成随机密钥，不覆盖原有密码或密钥。"""

from pathlib import Path
import secrets

from dotenv import dotenv_values

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def main() -> None:
    if not ENV_FILE.exists():
        raise SystemExit("请先将 .env.example 复制为 .env 并填写数据库密码")
    values = dotenv_values(ENV_FILE)
    if values.get("JWT_SECRET_KEY"):
        print(".env 已有 JWT_SECRET_KEY，保留原值（未显示密钥）")
        return
    # 追加最后一个同名项，dotenv 会使用最后的值，保留原文件其他内容。
    with ENV_FILE.open("a", encoding="utf-8") as stream:
        stream.write(f"\nJWT_SECRET_KEY={secrets.token_urlsafe(64)}\n")
    ENV_FILE.chmod(0o600)
    print("已将随机 JWT 密钥写入本机 .env（未显示密钥），原有配置保持不变")


if __name__ == "__main__":
    main()
