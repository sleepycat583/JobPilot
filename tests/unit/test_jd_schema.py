"""JD Schema 测试。"""

import pytest
from pydantic import ValidationError

from app.schemas.jd import JDParseInput, JDParsed, SkillRequirement


@pytest.mark.core_agent_tests
def test_jd_input_requires_minimum_length() -> None:
    with pytest.raises(ValidationError):
        JDParseInput.model_validate({"jd_text": "short"})


@pytest.mark.core_agent_tests
def test_skill_requirement_rejects_invalid_priority() -> None:
    with pytest.raises(ValidationError):
        SkillRequirement.model_validate(
            {
                "name": "Python",
                "category": "language",
                "priority": "critical",
                "evidence": "文中要求 Python",
            }
        )


@pytest.mark.core_agent_tests
def test_skill_requirement_requires_evidence() -> None:
    with pytest.raises(ValidationError):
        SkillRequirement.model_validate(
            {
                "name": "Python",
                "category": "language",
                "priority": "must",
            }
        )


@pytest.mark.core_agent_tests
def test_jd_parsed_requires_fields() -> None:
    with pytest.raises(ValidationError):
        JDParsed.model_validate(
            {
                "job_title": "Backend Engineer",
                "seniority": "mid",
                "company_name": None,
                "responsibilities": [],
                "skills": [],
                "experience_requirements": [],
                "education_requirements": [],
                "interview_focus": [],
                "company_context": [],
                "source_language": "zh-CN",
            }
        )
