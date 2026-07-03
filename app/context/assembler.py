from app.memory.schemas import MemoryBlock
from app.model_gateway.schemas import ChatMessage
from app.skills.schemas import InstalledSkill


class ContextAssembler:
    def __init__(self, base_prompt: str) -> None:
        self.base_prompt = base_prompt

    def assemble(
        self,
        skill: InstalledSkill | None,
        memories: list[MemoryBlock],
        history: list[ChatMessage],
        user_message: str,
    ) -> list[ChatMessage]:
        system_parts = [self.base_prompt]
        if skill is not None:
            system_parts.append(skill.private_prompt)
            if skill.output_expectation:
                system_parts.append(f"输出要求：{skill.output_expectation}")
        if memories:
            memory_lines = "\n".join(
                f"- [{memory.scope}] {memory.content}" for memory in memories
            )
            system_parts.append(f"<memories>\n{memory_lines}\n</memories>")

        return [
            ChatMessage(role="system", content="\n\n".join(system_parts)),
            *history,
            ChatMessage(role="user", content=user_message),
        ]
