"""Argon2 密码哈希和固定算法的 JWT 验证；不记录凭据。"""

from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_session
from app.models import User

password_hash = PasswordHash.recommended()
# 不存在的用户名也做一次相同哈希验证，避免立即返回造成明显时间差。
dummy_hash = password_hash.hash("unused-dummy-password")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token")
JWT_ALGORITHM = "HS256"
JWT_ISSUER = "campus-assistant"
JWT_AUDIENCE = "campus-assistant-api"


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="认证失败或令牌已过期，请重新登录",
        headers={"WWW-Authenticate": "Bearer"},
    )


def verify_password(password: str, hashed: str) -> bool:
    try:
        return password_hash.verify(password, hashed)
    except (UnknownHashError, ValueError):
        return False


def create_access_token(user_id: UUID) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user_id),
            "iat": now,
            "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "type": "access",
        },
        settings.jwt_secret_key.get_secret_value(),
        algorithm=JWT_ALGORITHM,
    )


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    session: Annotated[Session, Depends(get_session)],
) -> User:
    try:
        payload = jwt.decode(
            token,
            get_settings().jwt_secret_key.get_secret_value(),
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
            options={"require": ["sub", "exp", "iat", "iss", "aud", "type"]},
        )
        if payload["type"] != "access":
            raise unauthorized()
        user_id = UUID(payload["sub"])
    except (InvalidTokenError, ValueError, TypeError, AttributeError):
        raise unauthorized() from None
    user = session.get(User, user_id)
    if user is None:
        raise unauthorized()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
