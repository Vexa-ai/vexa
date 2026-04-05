from sqlalchemy import Column, String, Text, Integer, DateTime, ForeignKey, ARRAY
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func, text
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    name = Column(String(100))
    image_url = Column(Text)
    created_at = Column(DateTime, server_default=func.now(), default=func.now())
    max_concurrent_bots = Column(Integer, nullable=False, server_default='1', default=1)
    data = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=lambda: {})

    api_tokens = relationship("APIToken", back_populates="user")


class APIToken(Base):
    __tablename__ = "api_tokens"
    id = Column(Integer, primary_key=True, index=True)
    token = Column(String(255), unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    scopes = Column(ARRAY(Text), nullable=False, server_default=text("'{}'::text[]"))
    name = Column(String(255), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    last_used_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="api_tokens")
