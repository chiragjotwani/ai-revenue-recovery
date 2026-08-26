from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models.

    Postgres is the source of truth for this platform (ADR-001); every
    persisted domain concept must be modeled here, not inferred at runtime.
    """
