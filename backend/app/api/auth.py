"""Cookie-session authentication for the DocForensic dashboard."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select

from app.db.database import SessionRow, UserRow, session_factory

router = APIRouter(prefix="/auth", tags=["auth"])
SESSION_COOKIE = "docforensic_session"
SESSION_DAYS = 7
PBKDF2_ITERATIONS = 210_000


class SignupRequest(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    email: str = Field(min_length=5, max_length=320)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value or "." not in value.rsplit("@", 1)[-1]:
            raise ValueError("Enter a valid email address.")
        return value


class LoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=320)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value:
            raise ValueError("Enter a valid email address.")
        return value


class UserResponse(BaseModel):
    id: str
    name: str
    email: str
    role: str


def _hash_password(password: str, salt: bytes | None = None) -> str:
    actual_salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), actual_salt, PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${actual_salt.hex()}${digest.hex()}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, expected_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), expected_hex)
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _user_response(user: UserRow) -> UserResponse:
    return UserResponse(id=user.id, name=user.name, email=user.email, role=user.role)


async def _create_session(user_id: str, response: Response) -> None:
    token = secrets.token_urlsafe(40)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=SESSION_DAYS)
    async with session_factory()() as session:
        session.add(
            SessionRow(
                token_hash=_token_hash(token),
                user_id=user_id,
                created_at=now,
                expires_at=expires,
            )
        )
        await session.commit()
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=SESSION_DAYS * 24 * 60 * 60,
        path="/",
    )


async def _session_user(token: str | None) -> UserRow | None:
    if not token:
        return None
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with session_factory()() as session:
        row = (
            await session.execute(
                select(SessionRow, UserRow)
                .join(UserRow, UserRow.id == SessionRow.user_id)
                .where(SessionRow.token_hash == _token_hash(token))
            )
        ).first()
        if row is None:
            return None
        session_row, user = row
        expires_at = session_row.expires_at
        if expires_at < now:
            await session.delete(session_row)
            await session.commit()
            return None
        return user


@router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def signup(body: SignupRequest, response: Response) -> UserResponse:
    email = body.email.strip().lower()
    name = " ".join(body.name.strip().split())
    async with session_factory()() as session:
        existing = (
            await session.execute(select(UserRow).where(UserRow.email == email))
        ).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=409, detail="An account with this email already exists.")
        user = UserRow(
            id=uuid.uuid4().hex[:16],
            name=name,
            email=email,
            password_hash=_hash_password(body.password),
            role="Risk Analyst",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    await _create_session(user.id, response)
    return _user_response(user)


@router.post("/login", response_model=UserResponse)
async def login(body: LoginRequest, response: Response) -> UserResponse:
    email = body.email.strip().lower()
    async with session_factory()() as session:
        user = (
            await session.execute(select(UserRow).where(UserRow.email == email))
        ).scalar_one_or_none()
    if user is None or not _verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    await _create_session(user.id, response)
    return _user_response(user)


@router.get("/me", response_model=UserResponse)
async def me(docforensic_session: str | None = Cookie(default=None)) -> UserResponse:
    user = await _session_user(docforensic_session)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return _user_response(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    docforensic_session: str | None = Cookie(default=None),
) -> Response:
    if docforensic_session:
        async with session_factory()() as session:
            await session.execute(
                delete(SessionRow).where(
                    SessionRow.token_hash == _token_hash(docforensic_session)
                )
            )
            await session.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
