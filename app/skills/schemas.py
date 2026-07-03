from pydantic import BaseModel


class SkillManifest(BaseModel):
    skill_id: int
    skill_version_id: int
    name: str
    description: str
    tags: list[str]
    trigger_examples: list[str]
    output_expectation: str | None = None


class InstalledSkill(SkillManifest):
    private_prompt: str


class SkillRoutingResult(BaseModel):
    skill_id: int | None
    skill_version_id: int | None
    reason: str
    confidence: float
