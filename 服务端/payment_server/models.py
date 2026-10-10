from datetime import datetime

from sqlmodel import Field, SQLModel


class Device(SQLModel, table=True):
    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    device_id: str = Field(unique=True, index=True)
    token_hash: str = Field(unique=True, index=True)
    first_seen: datetime
    last_seen: datetime
    last_opened_at: datetime | None = Field(default=None, index=True)
    downloaded_video_count: int = Field(default=0, ge=0)
    expires_at: datetime
    paid_at: datetime | None = None
    blocked: bool = False


class Order(SQLModel, table=True):
    __tablename__ = "orders"

    order_id: str = Field(primary_key=True)
    device_id: str = Field(index=True)
    plan_id: str
    amount_cents: int
    grant_days: int
    status: str = Field(default="PENDING", index=True)
    created_at: datetime
    expires_at: datetime
    code_url: str | None = None
    last_checked_at: datetime | None = None
    transaction_id: str | None = Field(default=None, unique=True)
    paid_at: datetime | None = None
    granted_until: datetime | None = None
