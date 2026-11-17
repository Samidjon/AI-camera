from fastapi import APIRouter, Request

from app.api.deps import CurrentUser, DbDep, SettingsDep
from app.api.device_auth import client_ip
from app.core.ratelimit import limiter
from app.core.security import create_access_token
from app.schemas.auth import LoginRequest, TokenResponse, UserOut
from app.services.auth import authenticate

router = APIRouter()


@router.post("/login", response_model=TokenResponse, summary="Obtain an access token")
def login(body: LoginRequest, request: Request, db: DbDep, settings: SettingsDep) -> TokenResponse:
    ip = client_ip(request)
    # Brute-force protection before any password check (bcrypt is deliberately slow, too).
    limiter.enforce(f"login-ip:{ip}", settings.rate_login_per_min / 60, settings.rate_login_burst)
    limiter.enforce(f"login-acct:{body.email.strip().lower()}", settings.rate_login_account_per_hour / 3600,
                    settings.rate_login_account_burst)  # fmt: skip
    user = authenticate(db, body.email, body.password, ip=ip)
    # Permissions travel in the token so edge agents can authorize locally (offline, no call back here).
    claims = {"role": user.role.name, "perms": list(user.role.permissions or [])}
    token = create_access_token(str(user.id), settings, extra=claims)
    return TokenResponse(access_token=token, expires_in=settings.access_token_expire_minutes * 60)


@router.get("/me", response_model=UserOut, summary="Current user")
def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
