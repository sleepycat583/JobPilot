"""pytest 公共 fixture 占位文件。

该文件为后续 unit/ 与 integration/ 测试提供统一依赖注入入口。
当前步骤只建立最基础的 fixture 框架，不依赖任何业务实现。
"""

from pathlib import Path

import pytest


@pytest.fixture
def project_root() -> Path:
    """返回项目根目录路径，供后续测试定位配置和夹具文件。"""
    return Path(__file__).resolve().parent.parent


@pytest.fixture
def test_data_dir(project_root: Path) -> Path:
    """返回测试数据目录路径。

    当前仅提供统一入口，具体夹具内容将在后续步骤补充。
    """
    return project_root / "tests" / "fixtures"


@pytest.fixture
def temp_chroma_dir(tmp_path: Path) -> Path:
    """为后续 Chroma 相关测试提供临时目录。"""
    return tmp_path / "chroma"
