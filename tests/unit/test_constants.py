"""冻结常量的基础存在性与类型测试。"""

import pytest

from app import constants


@pytest.mark.core_agent_tests
def test_constants_exist_with_expected_types() -> None:
    """确认冻结常量存在且类型稳定，防止后续被误删或改错类型。"""

    assert isinstance(constants.EMBEDDING_MODEL, str)
    assert isinstance(constants.CHROMA_COLLECTION_NAME, str)
    assert isinstance(constants.RAG_TOP_K, int)
    assert isinstance(constants.RAG_RELEVANCE_THRESHOLD, float)
    assert isinstance(constants.LOW_SCORE_THRESHOLD, float)
    assert isinstance(constants.MAX_INPUT_LENGTH, int)
    assert isinstance(constants.MAX_FORMAT_RETRIES, int)
