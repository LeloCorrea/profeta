from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from typing import Optional
from app.db import Base
from sqlalchemy import Integer, Text, UniqueConstraint


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    telegram_username: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="inactive")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UserPreference(Base):
    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    preferred_hour: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    favorite_themes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    explanation_depth: Mapped[str] = mapped_column(String(32), default="balanced")
    preferred_delivery: Mapped[str] = mapped_column(String(32), default="text_audio")
    last_requested_theme: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class UserThemeInterest(Base):
    __tablename__ = "user_theme_interests"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    theme: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(32), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    plan_name: Mapped[str] = mapped_column(String(64), default="monthly")
    status: Mapped[str] = mapped_column(String(32), default="inactive")
    paid_until: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class VerseHistory(Base):
    __tablename__ = "verse_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[str] = mapped_column(String(64), index=True)
    book: Mapped[str] = mapped_column(String(128))
    chapter: Mapped[str] = mapped_column(String(32))
    verse: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(String(4000))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class FavoriteVerse(Base):
    __tablename__ = "favorite_verses"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    verse_id: Mapped[Optional[int]] = mapped_column(ForeignKey("verses.id"), nullable=True)
    book: Mapped[str] = mapped_column(String(128))
    chapter: Mapped[str] = mapped_column(String(32))
    verse: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(String(4000))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "book", "chapter", "verse", name="uq_favorite_verse_ref"),
    )

class ActivationToken(Base):
    __tablename__ = "activation_tokens"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    telegram_user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    asaas_payment_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    asaas_customer_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    asaas_payment_link_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    used_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), default="asaas")
    provider_payment_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    payment_link_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    amount: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    customer_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Verse(Base):
    __tablename__ = "verses"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    book: Mapped[str] = mapped_column(String(128), index=True)
    chapter: Mapped[int] = mapped_column(Integer, index=True)
    verse: Mapped[int] = mapped_column(Integer, index=True)

    text: Mapped[str] = mapped_column(Text)

    reference: Mapped[str] = mapped_column(String(128), unique=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("book", "chapter", "verse", name="uq_verse_ref"),
    )


class VerseExplanation(Base):
    __tablename__ = "verse_explanations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    book: Mapped[str] = mapped_column(String(128), index=True)
    chapter: Mapped[str] = mapped_column(String(32), index=True)
    verse: Mapped[str] = mapped_column(String(32), index=True)
    explanation: Mapped[str] = mapped_column(Text)
    source: Mapped[Optional[str]] = mapped_column(String(32), default="openai")
    is_fallback: Mapped[Optional[bool]] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UserJourney(Base):
    __tablename__ = "user_journeys"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    journey_key: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    current_step: Mapped[int] = mapped_column(Integer, default=0)
    last_touchpoint_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
