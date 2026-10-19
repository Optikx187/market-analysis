from logging.config import fileConfig
from alembic import context
from sqlalchemy import create_engine, pool

from app.config import settings


config = context.config
config.set_main_option(
    "sqlalchemy.url",
    settings.research_database_url.replace("%", "%%"),
)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def version_table_options() -> dict[str, str]:
    if settings.research_database_url.startswith("postgresql"):
        return {"version_table": "alembic_version_research"}
    return {}


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **version_table_options(),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=None,
            **version_table_options(),
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
