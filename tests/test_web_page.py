from fastapi.testclient import TestClient

from app.main import create_app


def test_root_serves_minimal_web_chat() -> None:
    client = TestClient(create_app())

    response = client.get("/")

    assert response.status_code == 200
    assert "Skill Runtime" in response.text
    assert "ReadableStream" in response.text
    assert "routing" in response.text


def test_root_page_creates_conversation_before_sending_message() -> None:
    client = TestClient(create_app())

    response = client.get("/")

    assert 'fetch("/chat/conversations"' in response.text
    assert "/chat/conversations/${id}/messages" in response.text
    assert "/chat/conversations/1/messages" not in response.text


def test_root_page_recovers_from_stale_stream_conversation() -> None:
    client = TestClient(create_app())

    response = client.get("/")

    assert 'eventName === "error"' in response.text
    assert "window.localStorage.removeItem(storageKey)" in response.text
    assert "retryOnStaleConversation" in response.text
    assert "sendMessage(text, true)" in response.text
