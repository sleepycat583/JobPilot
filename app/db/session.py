"""业务 Session 工厂。

本模块为业务 SQLite 提供请求级/任务级 Session 工厂，供 API、脚本和 Repository
复用；调用方必须自行管理事务边界，不跨线程共享 Session。
"""

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    """基于业务 Engine 创建 Session 工厂。"""

    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)