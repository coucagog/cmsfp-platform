# Database engine
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()


# --- Activation des foreign keys (SQLite) ---
# SQLite désactive les FK par défaut. On active PRAGMA foreign_keys=ON
# sur chaque nouvelle connexion via un listener sur l'engine synchrone.
@event.listens_for(engine.sync_engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    """Active PRAGMA foreign_keys=ON pour chaque connexion SQLite."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


async def get_db():
    async with async_session() as session:
        yield session
