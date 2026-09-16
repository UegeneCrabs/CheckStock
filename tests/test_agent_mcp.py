"""OAuth and MCP integration without production databases or marketplace calls."""

import asyncio
import base64
import hashlib
import json
import re
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI

from app.agents.mcp_store import OAuthError, digest
from app.dto.identity import Role, SectionAccessLevel, SectionName, User
from app.web.middleware import authentication_middleware
from app.web.routers import agent_analytics, agent_management, agent_mcp

BASE = "https://checkstock.test"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
VERIFIER = "a" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setenv("CHECKSTOCK_AGENT_PUBLIC_URL", BASE)
    monkeypatch.setenv("CHECKSTOCK_MCP_DB_PATH", str(tmp_path / "oauth.sqlite3"))
    users = {
        index: User(
            id=index,
            full_name=f"User {index}",
            login=f"user{index}",
            role=Role.USER,
            created_at=datetime.now(UTC),
            store_slugs=(store,),
            section_access={section: SectionAccessLevel.WRITE for section in SectionName},
        )
        for index, store in ((1, "rimili"), (2, "tris"))
    }
    identities = SimpleNamespace(
        get_user=lambda user_id: users.get(user_id.root),
        user_for_token=lambda token: users.get(int(token.value)) if token.value in {"1", "2"} else None,
    )
    app = FastAPI()
    app.state.container = SimpleNamespace(identity=identities)
    app.middleware("http")(authentication_middleware)
    app.include_router(agent_analytics.router)
    app.include_router(agent_management.router)
    app.include_router(agent_mcp.router)
    return app, users


def client(site, user=1):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=site[0]),
        base_url=BASE,
        cookies={"paketa_session": str(user)},
        follow_redirects=False,
    )


async def begin(browser, **overrides):
    registration = await browser.post(
        "/oauth/mcp/register", json={"redirect_uris": [CALLBACK], "client_name": "Claude"}
    )
    assert registration.status_code == 201, registration.text
    client_id = registration.json()["client_id"]
    params = {
        "client_id": client_id,
        "redirect_uri": CALLBACK,
        "response_type": "code",
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
        "state": "test-state",
        "resource": BASE + "/mcp",
        "scope": "analytics:read offline_access",
        **overrides,
    }
    consent = await browser.get("/oauth/mcp/authorize", params=params)
    return client_id, consent


async def authorize(browser, decision="approve"):
    client_id, consent = await begin(browser)
    assert consent.status_code == 200, consent.text
    assert consent.headers["referrer-policy"] == "same-origin"
    assert CALLBACK in consent.headers["content-security-policy"]
    pending = re.search(r'name="pending" value="([^"]+)"', consent.text)[1]
    response = await browser.post(
        "/oauth/mcp/authorize", data={"pending": pending, "decision": decision}, headers={"Origin": BASE}
    )
    assert response.status_code == 303, response.text
    values = parse_qs(urlsplit(response.headers["location"]).query)
    assert values["state"] == ["test-state"]
    return client_id, values


async def grant(browser):
    client_id, values = await authorize(browser)
    data = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "redirect_uri": CALLBACK,
        "code": values["code"][0],
        "code_verifier": VERIFIER,
        "resource": BASE + "/mcp",
    }
    response = await browser.post("/oauth/mcp/token", data=data)
    assert response.status_code == 200, response.text
    return client_id, response.json(), data


async def rpc(browser, token, method="tools/list", params=None):
    return await browser.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        headers={**MCP_HEADERS, "Authorization": "Bearer " + token},
    )


def test_oauth_discovery_and_full_tool_roundtrip(site):
    async def run():
        async with client(site) as browser:
            response = await browser.post("/mcp", json={})
            assert response.status_code == 401
            assert "/.well-known/oauth-protected-resource/mcp" in response.headers["www-authenticate"]
            metadata = (await browser.get("/.well-known/oauth-authorization-server")).json()
            assert metadata["code_challenge_methods_supported"] == ["S256"]
            assert (await browser.get("/.well-known/oauth-protected-resource/mcp")).json()[
                "resource"
            ] == BASE + "/mcp"
            _, tokens, _ = await grant(browser)
            token = tokens["access_token"]
            initialized = (await rpc(browser, token, "initialize", {"protocolVersion": "2025-11-25"})).json()
            assert initialized["result"]["protocolVersion"] == "2025-11-25"
            tools = (await rpc(browser, token)).json()["result"]["tools"]
            assert len(tools) > 10
            assert all(tool["annotations"]["readOnlyHint"] for tool in tools)
            stores = (await rpc(browser, token, "tools/call", {"name": "listAnalyticsStores"})).json()[
                "result"
            ]
            assert not stores["isError"]
            assert [store["slug"] for store in json.loads(stores["content"][0]["text"])] == ["rimili"]
            assert (
                await browser.get("/api/agent/v1/stores", headers={"Authorization": "Bearer " + token})
            ).status_code == 401
            connections = (await browser.get("/api/ai-agents/mcp/connections")).json()["connections"]
            assert len(connections) == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "changes",
    [
        {"redirect_uri": "https://evil.test/callback"},
        {"resource": "https://evil.test/mcp"},
        {"code_challenge_method": "plain"},
        {"code_challenge": "short"},
        {"scope": "analytics:write"},
    ],
)
def test_invalid_authorization_requests(site, changes):
    async def run():
        async with client(site) as browser:
            _, response = await begin(browser, **changes)
            assert response.status_code == 400
            assert "location" not in response.headers

    asyncio.run(run())


def test_consent_csrf_user_binding_and_denial(site):
    async def run():
        async with client(site) as browser:
            _, response = await begin(browser)
            pending = re.search(r'name="pending" value="([^"]+)"', response.text)[1]
            form = {"pending": pending, "decision": "approve"}
            assert (
                await browser.post("/oauth/mcp/authorize", data=form, headers={"Origin": "https://evil.test"})
            ).status_code == 403
            async with client(site, 2) as other:
                assert (await other.post("/oauth/mcp/authorize", data=form)).status_code == 400
            browser.cookies.delete(agent_mcp.COOKIE)
            assert (await browser.post("/oauth/mcp/authorize", data=form)).status_code == 400
            _, values = await authorize(browser, "deny")
            assert values["error"] == ["access_denied"]
            assert not agent_mcp.storage().connections(1)

    asyncio.run(run())


def test_code_binding_replay_and_refresh_reuse(site):
    async def run():
        async with client(site) as browser:
            client_id, values = await authorize(browser)
            data = {
                "grant_type": "authorization_code",
                "client_id": client_id,
                "redirect_uri": CALLBACK,
                "code": values["code"][0],
                "code_verifier": VERIFIER,
                "resource": BASE + "/mcp",
            }
            for change in (
                {"code_verifier": "b" * 64},
                {"redirect_uri": "https://evil.test"},
                {"resource": "https://evil.test/mcp"},
            ):
                assert (await browser.post("/oauth/mcp/token", data={**data, **change})).status_code == 400
            response = await browser.post("/oauth/mcp/token", data=data)
            tokens = response.json()
            assert response.status_code == 200
            assert (await browser.post("/oauth/mcp/token", data=data)).status_code == 400
            refresh = {
                "grant_type": "refresh_token",
                "client_id": client_id,
                "resource": BASE + "/mcp",
                "refresh_token": tokens["refresh_token"],
            }
            new = (await browser.post("/oauth/mcp/token", data=refresh)).json()
            assert new["refresh_token"] != tokens["refresh_token"]
            assert (await rpc(browser, new["access_token"], "ping")).status_code == 200
            assert (await browser.post("/oauth/mcp/token", data=refresh)).status_code == 400
            assert (await rpc(browser, new["access_token"], "ping")).status_code == 401

    asyncio.run(run())


def test_revocation_permissions_and_user_deactivation(site):
    async def run():
        async with client(site) as browser, client(site, 2) as other:
            client_id, tokens, _ = await grant(browser)
            connection = agent_mcp.storage().connections(1)[0]
            path = "/api/ai-agents/mcp/connections/" + connection["id"]
            assert (await other.delete(path, headers={"X-Agent-Management": "1"})).status_code == 404
            assert (await browser.delete(path)).status_code == 403
            assert (await browser.delete(path, headers={"X-Agent-Management": "1"})).status_code == 204
            assert (await rpc(browser, tokens["access_token"])).status_code == 401
            assert (
                await browser.post(
                    "/oauth/mcp/token",
                    data={
                        "grant_type": "refresh_token",
                        "client_id": client_id,
                        "refresh_token": tokens["refresh_token"],
                        "resource": BASE + "/mcp",
                    },
                )
            ).status_code == 400
            _, tokens, _ = await grant(browser)
            site[1][1] = site[1][1].model_copy(update={"is_active": False})
            assert (await rpc(browser, tokens["access_token"])).status_code == 401

    asyncio.run(run())


def test_mcp_validation_store_acl_and_concurrent_users(site):
    async def run():
        async with client(site) as first, client(site, 2) as second:
            (_, a, _), (_, b, _) = await asyncio.gather(grant(first), grant(second))
            results = await asyncio.gather(
                *[
                    rpc(
                        first if i % 2 else second,
                        a["access_token"] if i % 2 else b["access_token"],
                        "tools/call",
                        {"name": "listAnalyticsStores"},
                    )
                    for i in range(10)
                ]
            )
            for i, response in enumerate(results):
                stores = json.loads(response.json()["result"]["content"][0]["text"])
                assert [s["slug"] for s in stores] == (["rimili"] if i % 2 else ["tris"])
            for params in (
                {"name": "deleteStore"},
                {"name": "listAnalyticsStores", "arguments": {"url": "http://evil"}},
                {"name": "listAnalyticsStores", "arguments": []},
            ):
                assert (await rpc(first, a["access_token"], "tools/call", params)).json()["error"][
                    "code"
                ] == -32602
            blocked = (
                await rpc(
                    first,
                    a["access_token"],
                    "tools/call",
                    {
                        "name": "getAnalyticsStocks",
                        "arguments": {"store": "tris"},
                    },
                )
            ).json()["result"]
            assert blocked["isError"]
            assert (
                "Нет доступа" in blocked["content"][0]["text"]
                or "access" in blocked["content"][0]["text"].lower()
            )

    asyncio.run(run())


def test_expiry_and_hash_only_storage(site):
    async def run():
        async with client(site) as browser:
            _, tokens, code_data = await grant(browser)
            store = agent_mcp.storage()
            contents = store.path.read_bytes()
            for secret in (tokens["access_token"], tokens["refresh_token"], code_data["code"]):
                assert secret.encode() not in contents
            with store.transaction() as db:
                db.execute(
                    "UPDATE tokens SET expires = ? WHERE hash = ?",
                    (time.time() - 1, digest(tokens["access_token"])),
                )
            assert (await rpc(browser, tokens["access_token"])).status_code == 401

    asyncio.run(run())


def test_protocol_errors_and_no_login_redirect(site):
    async def run():
        async with client(site) as browser:
            _, tokens, _ = await grant(browser)
            headers = {**MCP_HEADERS, "Authorization": "Bearer " + tokens["access_token"]}
            assert (await browser.get("/mcp", headers=headers)).status_code == 405
            assert (
                await browser.post("/mcp", headers={**headers, "Origin": "https://evil.test"}, json={})
            ).status_code == 403
            assert (
                await browser.post("/mcp", headers={**headers, "MCP-Protocol-Version": "bad"}, json={})
            ).status_code == 400
            assert (await browser.post("/mcp", headers=headers, json=[])).json()["error"]["code"] == -32600
            assert (
                await browser.post(
                    "/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
                )
            ).status_code == 202
            assert (
                await browser.post(
                    "/mcp", headers={**headers, "Content-Type": "application/json"}, content=b"x" * 65537
                )
            ).status_code == 413

    asyncio.run(run())


def test_registration_and_disabled_configuration(site, monkeypatch):
    async def run():
        async with client(site) as browser:
            response = await browser.post(
                "/oauth/mcp/register", json={"redirect_uris": ["https://evil.test"]}
            )
            assert response.status_code == 400
            monkeypatch.delenv("CHECKSTOCK_AGENT_PUBLIC_URL")
            assert (await browser.post("/mcp", json={})).status_code == 503
            assert (await browser.get("/api/ai-agents/mcp/connections")).json()["url"] is None

    asyncio.run(run())


def test_store_atomic_code_redemption(site):
    from concurrent.futures import ThreadPoolExecutor

    store = agent_mcp.storage()
    cookie = "cookie"
    pending = store.pending(
        {
            "user_id": 1,
            "cookie_hash": digest(cookie),
            "client_id": "client",
            "redirect_uri": CALLBACK,
            "code_challenge": CHALLENGE,
            "resource": BASE + "/mcp",
            "scope": "analytics:read",
            "name": "Claude",
        }
    )
    code, _ = store.consent(pending, 1, cookie, True)

    def exchange(_):
        try:
            return store.exchange(code, "client", CALLBACK, CHALLENGE, BASE + "/mcp")
        except OAuthError:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(result is not None for result in pool.map(exchange, range(4))) == 1


def test_multiple_consent_tabs_do_not_invalidate_each_other(site):
    async def run():
        async with client(site) as browser:
            forms = []
            for _ in range(2):
                _, response = await begin(browser)
                forms.append(re.search(r'name="pending" value="([^"]+)"', response.text)[1])
            for pending in forms:
                response = await browser.post(
                    "/oauth/mcp/authorize",
                    data={"pending": pending, "decision": "approve"},
                    headers={"Origin": BASE},
                )
                assert response.status_code == 303
                assert "code=" in response.headers["location"]
                assert (
                    await browser.post(
                        "/oauth/mcp/authorize",
                        data={"pending": pending, "decision": "approve"},
                        headers={"Origin": BASE},
                    )
                ).status_code == 400

    asyncio.run(run())


def test_current_acl_changes_are_applied_without_reconnecting(site):
    async def run():
        async with client(site) as browser:
            _, tokens, _ = await grant(browser)
            site[1][1] = site[1][1].model_copy(update={"store_slugs": ("tris",)})
            result = (
                await rpc(browser, tokens["access_token"], "tools/call", {"name": "listAnalyticsStores"})
            ).json()["result"]
            assert [item["slug"] for item in json.loads(result["content"][0]["text"])] == ["tris"]
            access = dict(site[1][1].section_access)
            access[SectionName.AI_AGENTS] = SectionAccessLevel.NONE
            site[1][1] = site[1][1].model_copy(update={"section_access": access})
            assert (await rpc(browser, tokens["access_token"])).status_code == 401

    asyncio.run(run())


def test_read_only_agent_settings_cannot_issue_connections(site):
    async def run():
        access = dict(site[1][1].section_access)
        access[SectionName.AI_AGENTS] = SectionAccessLevel.READ
        site[1][1] = site[1][1].model_copy(update={"section_access": access})
        async with client(site) as browser:
            _, consent = await begin(browser)
            assert 'name="pending"' not in consent.text
            assert not agent_mcp.storage().connections(1)

    asyncio.run(run())


def test_expired_consent_code_and_grant(site):
    async def run():
        async with client(site) as browser:
            client_id, values = await authorize(browser)
            store = agent_mcp.storage()
            with store.transaction() as db:
                db.execute("UPDATE temporary SET expires = ?", (time.time() - 1,))
            response = await browser.post(
                "/oauth/mcp/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": client_id,
                    "redirect_uri": CALLBACK,
                    "code": values["code"][0],
                    "code_verifier": VERIFIER,
                    "resource": BASE + "/mcp",
                },
            )
            assert response.status_code == 400
            client_id, tokens, _ = await grant(browser)
            with store.transaction() as db:
                db.execute("UPDATE grants SET expires = ?", (time.time() - 1,))
            assert (await rpc(browser, tokens["access_token"])).status_code == 401
            assert (
                await browser.post(
                    "/oauth/mcp/token",
                    data={
                        "grant_type": "refresh_token",
                        "client_id": client_id,
                        "refresh_token": tokens["refresh_token"],
                        "resource": BASE + "/mcp",
                    },
                )
            ).status_code == 400

    asyncio.run(run())


def test_argument_types_and_api_validation(site):
    async def run():
        async with client(site) as browser:
            _, tokens, _ = await grant(browser)
            for arguments in (
                {"limit": True},
                {"limit": "2"},
                {"limit": 10**400},
                {"limit": 101},
                {"marketplace": "NOPE"},
                {"limit": 0},
            ):
                response = await rpc(
                    browser,
                    tokens["access_token"],
                    "tools/call",
                    {
                        "name": "getAnalyticsStocks",
                        "arguments": {"store": "rimili", **arguments},
                    },
                )
                assert response.json()["error"]["code"] == -32602
            response = await rpc(
                browser,
                tokens["access_token"],
                "tools/call",
                {
                    "name": "getLossMakingProducts",
                    "arguments": {"store": "rimili", "date_from": "bad", "date_to": "bad"},
                },
            )
            assert response.json()["result"]["isError"]

    asyncio.run(run())


def test_main_application_wiring_and_tools_match_rest_schema(site):
    from app.main import create_app

    async def run():
        main = create_app(container=site[0].state.container)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main), base_url=BASE) as browser:
            assert (await browser.post("/mcp", json={})).status_code == 401
            assert (await browser.get("/.well-known/oauth-authorization-server")).status_code == 200
            schema = (await browser.get("/api/agent/v1/openapi.json")).json()
            tools = await agent_mcp.catalog()
            assert set(tools) == {value["get"]["operationId"] for value in schema["paths"].values()} | {
                "getAnalyticsInstructions"
            }
            assert {"getAnalyticsProductAnalysis", "getAnalyticsProfitSummary"} <= set(tools)

    asyncio.run(run())


def test_full_rulebook_delivered_at_initialize_and_as_tool(site):
    async def run():
        async with client(site) as browser:
            _, tokens, _ = await grant(browser)
            initialized = (await rpc(browser, tokens["access_token"], "initialize")).json()["result"]
            result = (
                await rpc(browser, tokens["access_token"], "tools/call", {"name": "getAnalyticsInstructions"})
            ).json()["result"]
            text = result["content"][0]["text"]
            assert text == initialized["instructions"]
            for section in (
                "МАГАЗИН И ПЛОЩАДКА",
                "МЕНЕДЖЕР",
                "ТЕГ, НОВИНКА И РЕЙТИНГ",
                "ВСЕ ВИДЫ СТОКА",
                "ПЕРИОДЫ, СТРАНИЦЫ И ОШИБКИ",
                "СЕРВЕРНЫЙ ОТБОР",
                "РЕГЛАМЕНТ НОВИНОК",
                "УРОВЕНЬ ДЛЯ БИЗНЕСА",
                "ГИПОТЕЗЫ И РЕШЕНИЯ",
                "ПРИМЕНЕНИЕ В MCP",
            ):
                assert section in text
            assert "ключ Action" not in text
            assert "Profit.roi за тот же период" in text
            assert "все доступные магазины" in text
            tools = await agent_mcp.catalog()
            for name in ("getAnalyticsProfit", "getAnalyticsProfitSummary", "getAnalyticsProductAnalysis"):
                assert "getAnalyticsInstructions" in tools[name][1]["description"]

    asyncio.run(run())
