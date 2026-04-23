from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from trade_order_bridge.config import settings
from trade_order_bridge.database import get_db


def db_session(db: Session = Depends(get_db)) -> Session:
    return db


def require_admin_token(x_admin_token: str = Header(default="")) -> None:
    if not settings.admin_token or x_admin_token != settings.admin_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin token")
