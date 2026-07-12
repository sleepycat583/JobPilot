"""Review 最小冻结契约。

本文件只定义 §5.1 中引用到的 ReviewStatus 枚举，不实现 Week 2 的完整审核流程。
"""

from typing import Literal

ReviewStatus = Literal["pending", "in_review", "approved", "rejected", "revising"]
