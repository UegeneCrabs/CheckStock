import unittest
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dto.identity import User
from app.web.routers import agent_management, agent_mcp


class Elements(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.elements = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class InstructionPagesTests(unittest.TestCase):
    # Тест-кейс: Все страницы интеграций показывают общую навигацию и нужный компонент инструкции.
    def test_pages_share_navigation_and_instruction_component(self) -> None:
        """Тест-кейс: Все страницы интеграций показывают общую навигацию и нужный компонент инструкции.

        Ожидается: Все страницы интеграций показывают общую навигацию и нужный компонент инструкции.
        """

        # Given
        app = FastAPI()
        app.include_router(agent_management.router)
        user = User(id=1, login="test", full_name="Test", created_at=datetime.now(UTC))
        app.dependency_overrides[agent_management.owner] = lambda: user
        with patch.object(
            agent_management, "render_page", side_effect=lambda title, section, content, user: content
        ):
            with TestClient(app) as client:
                for active in ("keys", "catalog", "gpt", "instructions", "claude", "gpt-mcp"):
                    with self.subTest(active=active):
                        # When
                        response = client.get("/ai-agents/" + active)
                        # Then
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.headers["cache-control"], "no-store")
                        elements = Elements(response.text).elements
                        current = [
                            attrs["href"] for tag, attrs in elements if attrs.get("aria-current") == "page"
                        ]
                        self.assertEqual(current, ["/ai-agents/" + active])
                        links = [attrs.get("href") for tag, attrs in elements if tag == "a"]
                        self.assertIn("/ai-agents/instructions", links)
                        self.assertIn("/ai-agents/gpt-mcp", links)
                        if active in {"gpt", "instructions"}:
                            ids = [attrs.get("id") for tag, attrs in elements]
                            self.assertEqual(
                                ids.count("agent-instruction-text"), 1 if active == "instructions" else 0
                            )
                            expected_url = (
                                "/static/agents/agent-instructions-full.txt"
                                if active == "instructions"
                                else "/static/agents/agent-instructions.txt"
                            )
                            sources = [
                                attrs["data-instruction-url"]
                                for tag, attrs in elements
                                if "data-instruction-url" in attrs
                            ]
                            self.assertEqual(sources, [expected_url])
                            if active == "gpt":
                                self.assertIn(expected_url, links)
                            scripts = [attrs.get("src") for tag, attrs in elements if tag == "script"]
                            self.assertLess(
                                next(i for i, s in enumerate(scripts) if "clipboard.js" in s),
                                next(i for i, s in enumerate(scripts) if "instructions.js" in s),
                            )

    # Тест-кейс: Компактная версия подходит GPT; MCP получает полную версию без ограничения редактора GPT.
    def test_gpt_length_and_shared_mcp_rules(self) -> None:
        """Тест-кейс: Компактная версия подходит GPT; MCP получает полную версию без ограничения редактора GPT.

        Ожидается: Компактная версия подходит GPT; MCP получает полную версию без ограничения редактора GPT.
        """

        # Given
        compact = Path("static/agents/agent-instructions.txt").read_text(encoding="utf-8")
        full = Path("static/agents/agent-instructions-full.txt").read_text(encoding="utf-8")

        # When
        mcp = agent_mcp.analyst_instructions()

        # Then
        self.assertLessEqual(len(compact.replace("\n", "\r\n").encode("utf-16-le")) // 2, 8000)
        self.assertGreater(len(full), len(compact))
        self.assertEqual(mcp.split("\n\nПРИМЕНЕНИЕ В MCP\n", 1)[0], full.strip())
        self.assertNotEqual(mcp.split("\n\nПРИМЕНЕНИЕ В MCP\n", 1)[0], compact.strip())
