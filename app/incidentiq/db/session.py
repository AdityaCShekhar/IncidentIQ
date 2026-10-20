from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from incidentiq.core.config import Settings

SCHEMA_REVISION = "0003_knowledge_chunks"


def build_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=0,
        pool_timeout=2,
        connect_args={"connect_timeout": 2, "options": "-c statement_timeout=2000"},
        hide_parameters=True,
    )


def database_is_ready(engine: Engine) -> bool:
    try:
        with engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            return revision == SCHEMA_REVISION
    except SQLAlchemyError:
        return False
