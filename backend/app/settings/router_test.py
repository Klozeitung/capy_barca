"""
Tests for the API settings endpoints and Quick Print.

Requests to the printer go to an ``httpx.MockTransport`` installed in place of
the network, which records what would have been sent.
"""
import json
import uuid

import httpx
import pytest

import app.database.database as db_module
import app.settings.router as router_module
from app.blocks import repository as repo
from app.blocks.models import WORKSPACE_ROOT_ID, Block
from app.settings.model import CUSTOM_PRINTER_KEY, LOCAL_AI_KEY, AppSetting
from app.settings.secret_box import open_sealed
from app.users.model import User

PRINTER_URL = "http://host-x:1234/print"


class _Received(list):
    answer: dict


@pytest.fixture
def mock_printer(monkeypatch):
    received = _Received()
    received.answer = {"status": 200, "body": '{"done": true}'}

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(received.answer["status"], text=received.answer["body"])

    monkeypatch.setattr(router_module, "_transport", httpx.MockTransport(handler))
    return received


def _settings(**printer) -> dict:
    return {
        "local_ai": {"host": "", "key": None},
        "custom_printer": {
            "url": printer.get("url", PRINTER_URL),
            "text_field": printer.get("text_field", "text"),
            "arguments": printer.get("arguments", []),
        },
    }


def _page() -> uuid.UUID:
    block_id = uuid.uuid4()
    with db_module.SessionLocal() as db:
        db.add(Block(id=block_id, type="page", position=1.0, state="active",
                     content={"title": "Page A"}))
        db.commit()
    return block_id


def _account(username: str, *, can_print: bool) -> User:
    user = User(id=uuid.uuid4(), username=username, password_hash="x",
                role="member", is_active=True, can_print=can_print)
    with db_module.SessionLocal() as db:
        db.add(user)
        db.commit()
        db.refresh(user)
        db.expunge(user)
    return user


def _stored(key: str) -> dict:
    with db_module.SessionLocal() as db:
        row = db.get(AppSetting, key)
        return dict(row.value) if row else {}


# ─── GET / PUT /api/settings/api ─────────────────────────────────────────────


def test_settings_start_empty(http_client):
    response = http_client.get("/api/settings/api")
    assert response.status_code == 200
    assert response.json() == {
        "local_ai": {"host": "", "key_set": False},
        "custom_printer": {"url": "", "text_field": "text", "arguments": []},
    }


def test_settings_round_trip(http_client):
    payload = _settings(
        text_field="content",
        arguments=[
            {"name": "option-a", "target": "body", "value": "true"},
            {"name": "level", "target": "query", "value": "high"},
            {"name": "X-Token", "target": "header", "value": "token-a"},
        ],
    )
    payload["local_ai"] = {"host": "host-y:5678", "key": "key-a"}

    response = http_client.put("/api/settings/api", json=payload)

    assert response.status_code == 200
    assert response.json() == {
        "local_ai": {"host": "host-y:5678", "key_set": True},
        "custom_printer": {
            "url": PRINTER_URL,
            "text_field": "content",
            "arguments": [
                {"name": "option-a", "target": "body", "value": "true", "value_set": True},
                {"name": "level", "target": "query", "value": "high", "value_set": True},
                {"name": "X-Token", "target": "header", "value": None, "value_set": True},
            ],
        },
    }
    assert http_client.get("/api/settings/api").json() == response.json()


def test_secrets_are_stored_encrypted_and_never_returned(http_client):
    payload = _settings(arguments=[{"name": "X-Token", "target": "header", "value": "token-a"}])
    payload["local_ai"] = {"host": "host-y:5678", "key": "key-a"}

    response = http_client.put("/api/settings/api", json=payload)

    assert "token-a" not in response.text
    assert "key-a" not in response.text
    local_ai = _stored(LOCAL_AI_KEY)
    printer = _stored(CUSTOM_PRINTER_KEY)
    assert "key-a" not in json.dumps(local_ai)
    assert "token-a" not in json.dumps(printer)
    assert open_sealed(local_ai["key"]) == "key-a"
    assert open_sealed(printer["arguments"][0]["sealed"]) == "token-a"


def test_null_keeps_stored_secrets_and_empty_clears_the_key(http_client):
    first = _settings(arguments=[{"name": "X-Token", "target": "header", "value": "token-a"}])
    first["local_ai"] = {"host": "host-y:5678", "key": "key-a"}
    http_client.put("/api/settings/api", json=first)

    kept = _settings(arguments=[{"name": "x-token", "target": "header", "value": None}])
    kept["local_ai"] = {"host": "host-y:5678", "key": None}
    response = http_client.put("/api/settings/api", json=kept)

    assert response.json()["local_ai"]["key_set"] is True
    assert response.json()["custom_printer"]["arguments"][0]["value_set"] is True
    assert open_sealed(_stored(CUSTOM_PRINTER_KEY)["arguments"][0]["sealed"]) == "token-a"

    cleared = dict(kept, local_ai={"host": "host-y:5678", "key": ""})
    assert http_client.put("/api/settings/api", json=cleared).json()["local_ai"]["key_set"] is False


def test_a_new_header_without_a_value_is_not_set(http_client):
    payload = _settings(arguments=[{"name": "X-Token", "target": "header", "value": None}])
    response = http_client.put("/api/settings/api", json=payload)
    assert response.json()["custom_printer"]["arguments"][0]["value_set"] is False


def test_an_empty_url_disables_the_printer(http_client):
    http_client.put("/api/settings/api", json=_settings())
    http_client.put("/api/settings/api", json=_settings(url=""))
    assert http_client.get("/api/print/status").json()["configured"] is False


@pytest.mark.parametrize("printer", [
    {"url": "ftp://host-x/print"},
    {"url": "host-x:1234/print"},
    {"url": "http://"},
    {"text_field": "  "},
    {"arguments": [{"name": "text", "target": "body", "value": "x"}]},
    {"arguments": [{"name": "a", "target": "body", "value": "1"},
                   {"name": "a", "target": "body", "value": "2"}]},
    {"arguments": [{"name": "X-A", "target": "header", "value": "1"},
                   {"name": "x-a", "target": "header", "value": "2"}]},
    {"arguments": [{"name": "Bad Header", "target": "header", "value": "1"}]},
    {"arguments": [{"name": "a", "target": "cookie", "value": "1"}]},
    {"arguments": [{"name": "  ", "target": "query", "value": "1"}]},
])
def test_invalid_printer_settings_return_422(http_client, printer):
    assert http_client.put("/api/settings/api", json=_settings(**printer)).status_code == 422


def test_settings_are_admin_only(member_client):
    assert member_client.get("/api/settings/api").status_code == 403
    assert member_client.put("/api/settings/api", json=_settings()).status_code == 403
    assert member_client.post("/api/settings/api/printer-test").status_code == 403


# ─── POST /api/settings/api/printer-test ─────────────────────────────────────


def test_printer_test_sends_the_test_text_with_all_arguments(http_client, mock_printer):
    http_client.put("/api/settings/api", json=_settings(arguments=[
        {"name": "option-a", "target": "body", "value": "true"},
        {"name": "level", "target": "query", "value": "high"},
        {"name": "X-Token", "target": "header", "value": "token-a"},
    ]))

    response = http_client.post("/api/settings/api/printer-test")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "status": 200, "body": '{"done": true}'}
    [request] = mock_printer
    assert str(request.url) == PRINTER_URL + "?level=high"
    assert request.headers["X-Token"] == "token-a"
    assert json.loads(request.content) == {
        "option-a": True, "text": router_module.custom_printer.TEST_TEXT,
    }


def test_printer_test_counts_only_200_as_success(http_client, mock_printer):
    http_client.put("/api/settings/api", json=_settings())
    mock_printer.answer["status"] = 202
    assert http_client.post("/api/settings/api/printer-test").json()["ok"] is False


def test_printer_test_without_a_printer_returns_409(http_client):
    assert http_client.post("/api/settings/api/printer-test").status_code == 409


# ─── GET /api/print/status ───────────────────────────────────────────────────


def test_status_for_an_admin(http_client):
    assert http_client.get("/api/print/status").json() == {"configured": False, "allowed": True}
    http_client.put("/api/settings/api", json=_settings())
    assert http_client.get("/api/print/status").json() == {"configured": True, "allowed": True}


def test_status_for_members_follows_their_permission(http_client, login_as):
    http_client.put("/api/settings/api", json=_settings())
    login_as(_account("person-a", can_print=False))
    assert http_client.get("/api/print/status").json() == {"configured": True, "allowed": False}
    login_as(_account("person-b", can_print=True))
    assert http_client.get("/api/print/status").json() == {"configured": True, "allowed": True}


# ─── POST /api/print ─────────────────────────────────────────────────────────


def test_quick_print_forwards_the_text(http_client, mock_printer):
    http_client.put("/api/settings/api", json=_settings(text_field="content"))
    page_id = _page()

    response = http_client.post(
        "/api/print", json={"block_id": str(page_id), "text": "# Page A\n\nLine A"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    [request] = mock_printer
    assert json.loads(request.content) == {"content": "# Page A\n\nLine A"}


def test_quick_print_reports_a_printer_failure(http_client, mock_printer):
    http_client.put("/api/settings/api", json=_settings())
    mock_printer.answer.update(status=503, body='{"detail": "offline"}')

    response = http_client.post("/api/print", json={"block_id": str(_page()), "text": "Line A"})

    assert response.status_code == 200
    assert response.json() == {"ok": False, "status": 503, "body": '{"detail": "offline"}'}


def test_quick_print_by_a_member_with_permission(http_client, login_as, mock_printer):
    http_client.put("/api/settings/api", json=_settings())
    page_id = _page()
    login_as(_account("person-a", can_print=True))

    response = http_client.post("/api/print", json={"block_id": str(page_id), "text": "Line A"})

    assert response.status_code == 200
    assert len(mock_printer) == 1


def test_quick_print_without_permission_returns_403(http_client, login_as, mock_printer):
    http_client.put("/api/settings/api", json=_settings())
    page_id = _page()
    login_as(_account("person-a", can_print=False))

    response = http_client.post("/api/print", json={"block_id": str(page_id), "text": "Line A"})

    assert response.status_code == 403
    assert mock_printer == []


def test_quick_print_without_read_access_returns_403(http_client, login_as, mock_printer):
    http_client.put("/api/settings/api", json=_settings())
    page_id = _page()
    other = _account("person-b", can_print=True)
    # Restrict the page to the admin's own account; the member is not on it.
    response = http_client.put(
        f"/api/blocks/{page_id}/permissions",
        json={"mode": "whitelist", "grants": []},
    )
    assert response.status_code in (200, 204), response.text
    login_as(other)

    response = http_client.post("/api/print", json={"block_id": str(page_id), "text": "Line A"})

    assert response.status_code == 403
    assert mock_printer == []


def test_quick_print_for_an_unknown_block_returns_404(http_client, mock_printer):
    http_client.put("/api/settings/api", json=_settings())
    response = http_client.post("/api/print", json={"block_id": str(uuid.uuid4()), "text": "Line A"})
    assert response.status_code == 404
    assert mock_printer == []


def test_quick_print_without_a_printer_returns_409(http_client):
    response = http_client.post("/api/print", json={"block_id": str(_page()), "text": "Line A"})
    assert response.status_code == 409


@pytest.mark.parametrize("text", ["", "   \n ", "x" * 20_001])
def test_quick_print_rejects_blank_or_oversized_text(http_client, text):
    http_client.put("/api/settings/api", json=_settings())
    response = http_client.post("/api/print", json={"block_id": str(_page()), "text": text})
    assert response.status_code == 422


def test_quick_print_requires_a_session():
    from fastapi.testclient import TestClient

    from app.main import app

    response = TestClient(app).post(
        "/api/print", json={"block_id": str(uuid.uuid4()), "text": "Line A"},
    )
    assert response.status_code == 401


# ─── Family set nuances ──────────────────────────────────────────────────────


def _family_database(client) -> tuple[str, dict]:
    db_id = client.post("/api/blocks", json={"type": "database", "parent_id": str(WORKSPACE_ROOT_ID)}).json()["id"]
    resp = client.post(
        f"/api/databases/{db_id}/views", json={"name": "Tree", "view_type": "family_tree"},
    )
    assert resp.status_code == 201, resp.text
    schemas = {s["name"]: s for s in client.get(f"/api/databases/{db_id}/schemas").json()}
    return db_id, schemas


def test_kinship_nuances_list_the_base_nuances_per_group(http_client):
    response = http_client.get(
        "/api/settings/kinship-nuances", headers={"Accept-Language": "de"},
    )
    assert response.status_code == 200
    groups = {g["group"]: g for g in response.json()["groups"]}
    assert list(groups) == ["parents", "partners", "siblings", "half_siblings"]
    assert groups["parents"]["bases"][0] == {"id": "kin.parent.biological", "label": "leiblich"}
    assert groups["siblings"]["bases"] == []
    assert all(g["aliases"] == [] for g in groups.values())


def test_kinship_aliases_reach_the_properties_of_the_set(http_client):
    db_id, _ = _family_database(http_client)
    response = http_client.put("/api/settings/kinship-nuances", json={"aliases": [
        {"group": "parents", "base": "kin.parent.adoptive", "label": "Label A"},
        {"group": "siblings", "label": "Label B"},
    ]})
    assert response.status_code == 200, response.text
    groups = {g["group"]: g for g in response.json()["groups"]}
    alias = groups["parents"]["aliases"][0]
    assert alias["base"] == "kin.parent.adoptive" and alias["label"] == "Label A"

    schemas = {s["name"]: s for s in http_client.get(f"/api/databases/{db_id}/schemas").json()}
    assert schemas["Children"]["config"]["nuance"]["options"][-1] == {"id": alias["id"], "label": "Label A"}
    assert schemas["Siblings"]["config"]["nuance"]["options"] == [
        {"id": groups["siblings"]["aliases"][0]["id"], "label": "Label B"},
    ]


def test_kinship_alias_rejects_a_label_of_a_base_nuance(http_client):
    response = http_client.put("/api/settings/kinship-nuances", json={"aliases": [
        {"group": "partners", "base": "kin.partner.affair", "label": "Ehe"},
    ]})
    assert response.status_code == 422


def test_deleting_an_alias_moves_its_values_to_the_base_nuance(http_client):
    db_id, schemas = _family_database(http_client)
    stored = http_client.put("/api/settings/kinship-nuances", json={"aliases": [
        {"group": "parents", "base": "kin.parent.step", "label": "Label A"},
        {"group": "siblings", "label": "Label B"},
    ]}).json()
    groups = {g["group"]: g for g in stored["groups"]}
    parent_alias = groups["parents"]["aliases"][0]["id"]
    sibling_alias = groups["siblings"]["aliases"][0]["id"]

    entry_a = http_client.post(f"/api/databases/{db_id}/entries").json()["id"]
    entry_b = http_client.post(f"/api/databases/{db_id}/entries").json()["id"]
    parents = schemas["Parents"]["id"]
    siblings = schemas["Siblings"]["id"]
    with db_module.SessionLocal() as db:
        repo.upsert_value(db, page_id=uuid.UUID(entry_a), schema_id=uuid.UUID(parents), value={
            "relationPool": {entry_b: [""]}, "nuancePool": {entry_b: {"": parent_alias}},
            "_timeline": {"": {"related_ids": [entry_b], "nuances": {entry_b: parent_alias}}},
        })
        repo.upsert_value(db, page_id=uuid.UUID(entry_a), schema_id=uuid.UUID(siblings), value={
            "related_ids": [entry_b], "nuances": {entry_b: sibling_alias},
        })
        db.commit()

    usage = http_client.get(f"/api/settings/kinship-nuances/{parent_alias}/usage")
    assert usage.json() == {"entries": 1}
    assert http_client.get("/api/settings/kinship-nuances/unknown/usage").status_code == 404

    response = http_client.put("/api/settings/kinship-nuances", json={"aliases": []})
    assert response.status_code == 200, response.text
    with db_module.SessionLocal() as db:
        parent_value = repo.get_value(db, uuid.UUID(entry_a), uuid.UUID(parents)).value
        sibling_value = repo.get_value(db, uuid.UUID(entry_a), uuid.UUID(siblings)).value
    assert parent_value["nuancePool"] == {entry_b: {"": "kin.parent.step"}}
    assert parent_value["_timeline"][""]["nuances"] == {entry_b: "kin.parent.step"}
    assert "nuances" not in sibling_value


def test_kinship_nuances_are_admin_only(http_client, login_as):
    login_as(_account("member-a", can_print=False))
    assert http_client.get("/api/settings/kinship-nuances").status_code == 403
    assert http_client.put("/api/settings/kinship-nuances", json={"aliases": []}).status_code == 403
