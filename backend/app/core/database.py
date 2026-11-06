from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings

engine = create_async_engine(settings.METADATA_DB_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    async with engine.begin() as conn:
        from app.models import connection as _c, variable as _v, agent_provider as _ap, run_history as _rh  # noqa
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)
        from app.services.connection_service import encrypt_plaintext_extras
        await conn.run_sync(encrypt_plaintext_extras)


def _add_missing_columns(sync_conn) -> None:
    """create_all never alters an existing table, so a column added to a model
    is added here (nullable, no default) for databases created before it."""
    import sqlalchemy as sa
    insp = sa.inspect(sync_conn)
    for table in Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name not in have:
                ddl = col.type.compile(dialect=sync_conn.dialect)
                sync_conn.exec_driver_sql(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {ddl}')


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
