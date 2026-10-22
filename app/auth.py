"""注册、OAuth2 表单登录与当前用户查询。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_session
from app.models import User
from app.schemas import RegisterRequest, TokenResponse, UserRead
from app.security import (
    CurrentUser, create_access_token, dummy_hash, password_hash,
    unauthorized, verify_password,
)

router = APIRouter(tags=["用户认证"])
SessionDep = Annotated[Session, Depends(get_session)]


@router.post("/auth/register", response_model=UserRead, status_code=201, summary="注册用户")
def register(data: RegisterRequest, session: SessionDep) -> User:
    user = User(
        username=data.username,
        hashed_password=password_hash.hash(data.password.get_secret_value()),
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) == "uq_users_username":
            raise HTTPException(status_code=409, detail="用户名已存在") from None
        raise
    return user


@router.post("/auth/token", response_model=TokenResponse, summary="登录并获取访问令牌")
def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: SessionDep,
    response: Response,
) -> TokenResponse:
    if len(form.username) > 64 or len(form.password) > 128:
        raise unauthorized()
    user = session.scalar(select(User).where(User.username == form.username.strip().lower()))
    valid = verify_password(form.password, user.hashed_password if user else dummy_hash)
    if not valid or user is None:
        raise unauthorized()
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return TokenResponse(
        access_token=create_access_token(user.id),
        expires_in=get_settings().access_token_expire_minutes * 60,
    )


@router.get("/users/me", response_model=UserRead, summary="查询当前用户")
def read_me(user: CurrentUser) -> User:
    return user
