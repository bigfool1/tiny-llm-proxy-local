from app.model_gateway.schemas import ChatMessage


class ContextTrimmer:
    def __init__(self, max_chars: int) -> None:
        self.max_chars = max_chars

    def trim(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        if len(messages) <= 2:
            return messages

        system = messages[0]
        current_user = messages[-1]
        kept_middle: list[ChatMessage] = []
        total = len(system.content) + len(current_user.content)

        for message in reversed(messages[1:-1]):
            candidate_total = total + len(message.content)
            if candidate_total > self.max_chars:
                continue
            kept_middle.append(message)
            total = candidate_total

        kept_middle.reverse()
        return [system, *kept_middle, current_user]
