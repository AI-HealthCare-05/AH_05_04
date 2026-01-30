from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.models.users import User
from app.repositories.user_repository import UserRepository
from app.services.jwt import JwtService

security = HTTPBearer()


async def get_request_user(credential: Annotated[HTTPAuthorizationCredentials, Depends(security)]) -> User:
    token = credential.credentials
    verified = JwtService().verify_jwt(token=token, token_type="access")
    user_id = verified.payload["user_id"]
    user = await UserRepository().get_user(user_id)
    if not user:
        raise HTTPException(detail="Authenticate Failed.", status_code=status.HTTP_401_UNAUTHORIZED)
    return user
