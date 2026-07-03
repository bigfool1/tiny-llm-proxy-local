import json
from typing import Protocol

from app.model_gateway.schemas import ChatMessage, ModelCompletion
from app.skills.schemas import InstalledSkill, SkillRoutingResult


class RoutingModelGateway(Protocol):
    async def complete(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
    ) -> ModelCompletion: ...


class SkillRouter:
    def __init__(self, model_gateway: RoutingModelGateway | None) -> None:
        self.model_gateway = model_gateway

    async def route(
        self,
        message: str,
        skills: list[InstalledSkill],
    ) -> SkillRoutingResult:
        if not skills:
            return SkillRoutingResult(
                skill_id=None,
                skill_version_id=None,
                reason="没有可用 skill",
                confidence=0.0,
            )

        lowered_message = message.lower()
        for skill in skills:
            for tag in skill.tags:
                if tag.lower() in lowered_message:
                    return SkillRoutingResult(
                        skill_id=skill.skill_id,
                        skill_version_id=skill.skill_version_id,
                        reason=f"命中 skill tag: {tag}",
                        confidence=0.75,
                    )

        if self.model_gateway is not None:
            manifests = [
                {
                    "skill_id": skill.skill_id,
                    "skill_version_id": skill.skill_version_id,
                    "name": skill.name,
                    "description": skill.description,
                    "tags": skill.tags,
                    "trigger_examples": skill.trigger_examples,
                }
                for skill in skills
            ]
            manifest_json = json.dumps(manifests, ensure_ascii=False)
            prompt = (
                "根据用户消息和可用 skill manifest 选择最合适的 skill。"
                "如果没有明显匹配，返回 no_skill。只输出 JSON。"
                f"\n用户消息：{message}\n可用 skill：{manifest_json}"
            )
            completion = await self.model_gateway.complete(
                [ChatMessage(role="user", content=prompt)]
            )
            try:
                data = json.loads(completion.text)
            except json.JSONDecodeError:
                return SkillRoutingResult(
                    skill_id=None,
                    skill_version_id=None,
                    reason="routing JSON 解析失败",
                    confidence=0.0,
                )
            selected_id = data.get("skill_id")
            for skill in skills:
                if selected_id == skill.skill_id:
                    return SkillRoutingResult(
                        skill_id=skill.skill_id,
                        skill_version_id=skill.skill_version_id,
                        reason=str(data.get("reason", "LLM routing 命中 skill")),
                        confidence=float(data.get("confidence", 0.5)),
                    )
            if selected_id is None:
                return SkillRoutingResult(
                    skill_id=None,
                    skill_version_id=None,
                    reason=str(data.get("reason", "LLM routing 未命中 skill")),
                    confidence=float(data.get("confidence", 0.0)),
                )

        return SkillRoutingResult(
            skill_id=None,
            skill_version_id=None,
            reason="未命中任何 skill tag",
            confidence=0.0,
        )
