from app.context.assembler import ContextAssembler
from app.context.trimmer import ContextTrimmer
from app.memory.schemas import MemoryBlock
from app.model_gateway.schemas import ChatMessage
from app.skills.schemas import InstalledSkill


def test_context_assembler_orders_system_skill_memory_history_user() -> None:
    skill = InstalledSkill(
        skill_id=1,
        skill_version_id=2,
        name="contract-reviewer",
        description="审合同",
        tags=["合同"],
        trigger_examples=[],
        private_prompt="你是合同审查专家。",
        output_expectation="输出风险清单。",
    )
    assembler = ContextAssembler(base_prompt="你是 Web Chat Runtime。")

    messages = assembler.assemble(
        skill=skill,
        memories=[MemoryBlock(scope="user", content="用户偏好中文。", score=0.8)],
        history=[ChatMessage(role="assistant", content="上一轮回复")],
        user_message="请审查合同",
    )

    assert messages[0].role == "system"
    assert "你是 Web Chat Runtime。" in messages[0].content
    assert "你是合同审查专家。" in messages[0].content
    assert "输出要求：输出风险清单。" in messages[0].content
    assert "<memories>" in messages[0].content
    assert "- [user] 用户偏好中文。" in messages[0].content
    assert messages[1] == ChatMessage(role="assistant", content="上一轮回复")
    assert messages[-1] == ChatMessage(role="user", content="请审查合同")


def test_context_assembler_omits_skill_and_memory_sections_when_absent() -> None:
    assembler = ContextAssembler(base_prompt="base")

    messages = assembler.assemble(
        skill=None,
        memories=[],
        history=[],
        user_message="hello",
    )

    assert messages == [
        ChatMessage(role="system", content="base"),
        ChatMessage(role="user", content="hello"),
    ]


def test_context_trimmer_keeps_system_and_current_user() -> None:
    trimmer = ContextTrimmer(max_chars=30)
    messages = [
        ChatMessage(role="system", content="system prompt"),
        ChatMessage(role="user", content="old message that is very long"),
        ChatMessage(role="assistant", content="old answer that is very long"),
        ChatMessage(role="user", content="current"),
    ]

    trimmed = trimmer.trim(messages)

    assert trimmed[0].role == "system"
    assert trimmed[-1].content == "current"


def test_context_trimmer_keeps_recent_middle_messages_first() -> None:
    trimmer = ContextTrimmer(max_chars=29)
    messages = [
        ChatMessage(role="system", content="system"),
        ChatMessage(role="user", content="old-111111"),
        ChatMessage(role="assistant", content="recent-22"),
        ChatMessage(role="user", content="current"),
    ]

    trimmed = trimmer.trim(messages)

    assert trimmed == [
        ChatMessage(role="system", content="system"),
        ChatMessage(role="assistant", content="recent-22"),
        ChatMessage(role="user", content="current"),
    ]
