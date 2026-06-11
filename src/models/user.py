import uuid
from sqlalchemy import Column, String
from sqlalchemy.dialects.postgresql import UUID

from ..core.database import Base


class User(Base):
    """Minimal User model — only fields needed by this service."""
    __tablename__ = "users"
    __table_args__ = {"extend_existing": True}

    user_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(255), nullable=True)
    email = Column(String(255), nullable=False, unique=True, index=True)

