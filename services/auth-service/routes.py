import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pkg.config import get_settings
from pkg.database import get_db_session
from pkg.logger import get_logger
from pkg.models.user import User
from pkg.security import create_access_token, decode_access_token, hash_password, verify_password

try:
    from .schemas import (
        TokenResponse,
        TokenVerifyRequest,
        TokenVerifyResponse,
        UserLoginRequest,
        UserRegisterRequest,
        UserResponse,
    )
except ImportError:
    from schemas import (
        TokenResponse,
        TokenVerifyRequest,
        TokenVerifyResponse,
        UserLoginRequest,
        UserRegisterRequest,
        UserResponse,
    )

logger = get_logger("auth-service")
settings = get_settings()
router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_user(req: UserRegisterRequest, db: AsyncSession = Depends(get_db_session)):
    stmt = select(User).where(User.email == req.email)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User with this email already exists"
        )

    user = User(
        id=str(uuid.uuid4()),
        email=req.email,
        hashed_password=hash_password(req.password),
        full_name=req.full_name,
        role="user",
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    logger.info(f"User registered successfully: {user.email}")
    return user


@router.post("/login", response_model=TokenResponse)
async def login_user(req: UserLoginRequest, db: AsyncSession = Depends(get_db_session)):
    stmt = select(User).where(User.email == req.email)
    user = (await db.execute(stmt)).scalar_one_or_none()

    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated"
        )

    token = create_access_token({
        "sub": user.id,
        "email": user.email,
        "role": user.role,
    })

    logger.info(f"User logged in successfully: {user.email}")
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in_minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
    )


@router.post("/verify", response_model=TokenVerifyResponse)
async def verify_token(req: TokenVerifyRequest):
    try:
        payload = decode_access_token(req.token)
        return TokenVerifyResponse(
            valid=True,
            user_id=payload.get("sub"),
            email=payload.get("email"),
            role=payload.get("role"),
        )
    except Exception as e:
        return TokenVerifyResponse(valid=False, error=str(e))


@router.get("/me", response_model=UserResponse)
async def get_current_user(token: str, db: AsyncSession = Depends(get_db_session)):
    try:
        payload = decode_access_token(token)
        user_id = payload.get("sub")
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e

    stmt = select(User).where(User.id == user_id)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user
