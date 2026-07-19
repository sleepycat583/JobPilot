"""Alembic 环境配置。

本模块只负责把业务 ORM metadata 绑定到 Alembic，并使用业务 SQLite URL 执行迁移；
不会连接或修改 LangGraph Checkpoint 数据库。
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import load_settings
from app.db.base import Base
from app.db import models  # noqa: F401  # 确保 Alembic 能发现全部 ORM 模型

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
settings = load_settings()
config.set_main_option("sqlalchemy.url", settings.sqlalchemy_database_url)


def run_migrations_offline() -> None:
    """离线模式下执行业务库迁移。"""

    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式下执行业务库迁移。"""

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()