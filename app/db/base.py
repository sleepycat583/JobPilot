"""SQLAlchemy Declarative Base。

本模块定义业务 SQLite 的 ORM 基类，供模型声明与 Alembic metadata 发现复用；
不包含连接、会话或运行时副作用。
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """业务 ORM 的统一基类。"""
