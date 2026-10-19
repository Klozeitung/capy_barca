"""
Tests for the view endpoints.

Run against the PostgreSQL test database through the ``http_client`` fixture,
which authenticates as an admin. Permission scenarios build a member client
of their own.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

import app.database.database as db_module
from app.blocks.models import WORKSPACE_ROOT_ID, DatabaseView
from app.main import app
from app.permissions import repository as perm_repo
from app.session.deps import get_current_user, require_session
from app.users.model import User


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _database(client) -> str:
    resp = client.post("/api/blocks", json={"type": "database", "parent_id": str(WORKSPACE_ROOT_ID)})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _schema(client, database_id, name, type_="text", config=None, visible_in_view_id=None) -> dict:
    body = {"name": name, "type": type_}
    if config is not None:
        body["config"] = config
    if visible_in_view_id is not None:
        body["visible_in_view_id"] = visible_in_view_id
    resp = client.post(f"/api/databases/{database_id}/schemas", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _entry(client, database_id, title) -> str:
    resp = client.post(f"/api/databases/{database_id}/entries")
    assert resp.status_code == 201, resp.text
    entry_id = resp.json()["id"]
    client.patch(f"/api/blocks/{entry_id}", json={"content": {"title": title}})
    return entry_id


def _value(client, database_id, entry_id, schema_id, value) -> None:
    resp = client.put(
        f"/api/databases/{database_id}/entries/{entry_id}/values/{schema_id}",
        json={"value": value},
    )
    assert resp.status_code == 200, resp.text


def _views(client, database_id, placement_id=None) -> dict:
    url = f"/api/databases/{database_id}/views"
    if placement_id:
        url += f"?placement_id={placement_id}"
    resp = client.get(url)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _patch(client, database_id, view_id, **changes):
    return client.patch(f"/api/databases/{database_id}/views/{view_id}", json=changes)


def _page(client, database_id, view_id, **body) -> dict:
    resp = client.post(f"/api/databases/{database_id}/views/{view_id}/entries", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _titles(page) -> list[str]:
    return [e["content"]["title"] for e in page["entries"]]


# ─── Bootstrap ───────────────────────────────────────────────────────────────


def test_first_request_creates_a_default_view(http_client):
    database_id = _database(http_client)
    data = _views(http_client, database_id)
    assert len(data["views"]) == 1
    view = data["views"][0]
    assert view["name"] == ""
    assert view["view_type"] == "table"
    assert data["selected_view_id"] == view["id"]


def test_first_request_creates_the_system_properties_hidden(http_client):
    database_id = _database(http_client)
    view = _views(http_client, database_id)["views"][0]
    schemas = http_client.get(f"/api/databases/{database_id}/schemas").json()
    system = {s["id"] for s in schemas if s["type"] in ("id", "created_time", "parent_item")}
    assert system
    assert system <= set(view["settings"]["hiddenColumns"])
    assert view["settings"]["colOrder"][0] == "__name__"


def test_repeated_requests_do_not_add_views(http_client):
    database_id = _database(http_client)
    _views(http_client, database_id)
    assert len(_views(http_client, database_id)["views"]) == 1


def test_unknown_database_answers_404(http_client):
    assert http_client.get(f"/api/databases/{uuid.uuid4()}/views").status_code == 404


# ─── Create, duplicate, order, delete ────────────────────────────────────────


def test_created_view_is_appended_with_the_default_layout(http_client):
    database_id = _database(http_client)
    first = _views(http_client, database_id)["views"][0]
    resp = http_client.post(f"/api/databases/{database_id}/views", json={"name": "View B"})
    assert resp.status_code == 201
    created = resp.json()
    assert created["position"] > first["position"]
    assert created["settings"]["colOrder"][0] == "__name__"


def test_unknown_view_type_is_refused(http_client):
    database_id = _database(http_client)
    resp = http_client.post(f"/api/databases/{database_id}/views", json={"view_type": "spiral"})
    assert resp.status_code == 422


def test_malformed_filter_is_refused(http_client):
    database_id = _database(http_client)
    resp = http_client.post(
        f"/api/databases/{database_id}/views",
        json={"filter_groups": [{"conjunction": "and", "filters": [{"operator": "eq"}]}]},
    )
    assert resp.status_code == 422


def test_duplicate_is_placed_right_after_the_source(http_client):
    database_id = _database(http_client)
    first = _views(http_client, database_id)["views"][0]
    second = http_client.post(f"/api/databases/{database_id}/views", json={"name": "View B"}).json()
    _patch(http_client, database_id, first["id"], sorts=[{"schemaId": "__name__", "direction": "desc"}])

    resp = http_client.post(
        f"/api/databases/{database_id}/views/{first['id']}/duplicate", json={"name": "Copy"},
    )

    assert resp.status_code == 201
    copy = resp.json()
    assert copy["name"] == "Copy"
    assert copy["sorts"] == [{"schemaId": "__name__", "direction": "desc"}]
    assert first["position"] < copy["position"] < second["position"]


def test_order_is_applied(http_client):
    database_id = _database(http_client)
    first = _views(http_client, database_id)["views"][0]["id"]
    second = http_client.post(f"/api/databases/{database_id}/views", json={}).json()["id"]

    resp = http_client.put(
        f"/api/databases/{database_id}/views/order", json={"view_ids": [second, first]},
    )

    assert resp.status_code == 200
    assert [v["id"] for v in _views(http_client, database_id)["views"]] == [second, first]


def test_order_must_name_every_view(http_client):
    database_id = _database(http_client)
    first = _views(http_client, database_id)["views"][0]["id"]
    http_client.post(f"/api/databases/{database_id}/views", json={})
    resp = http_client.put(f"/api/databases/{database_id}/views/order", json={"view_ids": [first]})
    assert resp.status_code == 422


def test_the_last_view_cannot_be_deleted(http_client):
    database_id = _database(http_client)
    only = _views(http_client, database_id)["views"][0]["id"]
    assert http_client.delete(f"/api/databases/{database_id}/views/{only}").status_code == 409


def test_deleting_the_selected_view_falls_back_to_the_first(http_client):
    database_id = _database(http_client)
    first = _views(http_client, database_id)["views"][0]["id"]
    second = http_client.post(f"/api/databases/{database_id}/views", json={}).json()["id"]
    http_client.put(
        f"/api/databases/{database_id}/views/selection",
        json={"placement_id": database_id, "view_id": second},
    )

    assert http_client.delete(f"/api/databases/{database_id}/views/{second}").status_code == 204
    assert _views(http_client, database_id)["selected_view_id"] == first


# ─── Partial updates ─────────────────────────────────────────────────────────


def test_settings_are_merged_key_by_key(http_client):
    database_id = _database(http_client)
    view_id = _views(http_client, database_id)["views"][0]["id"]

    _patch(http_client, database_id, view_id, settings={"colWidths": {"__name__": 240}})
    _patch(http_client, database_id, view_id, settings={"stickyHeader": False})
    settings = _patch(http_client, database_id, view_id, settings={"frozenColumns": 2}).json()["settings"]

    assert settings["colWidths"] == {"__name__": 240}
    assert settings["stickyHeader"] is False
    assert settings["frozenColumns"] == 2
    assert settings["colOrder"][0] == "__name__"


def test_a_settings_key_sent_as_null_is_removed(http_client):
    database_id = _database(http_client)
    view_id = _views(http_client, database_id)["views"][0]["id"]
    _patch(http_client, database_id, view_id, settings={"groupBySchemaId": "__name__"})

    settings = _patch(http_client, database_id, view_id, settings={"groupBySchemaId": None}).json()["settings"]

    assert "groupBySchemaId" not in settings


def test_invalid_settings_leave_the_view_unchanged(http_client):
    database_id = _database(http_client)
    view_id = _views(http_client, database_id)["views"][0]["id"]
    _patch(http_client, database_id, view_id, settings={"frozenColumns": 1})

    assert _patch(http_client, database_id, view_id, settings={"frozenColumns": 9}).status_code == 422
    assert _patch(http_client, database_id, view_id, settings={"unknownKey": 1}).status_code == 422
    view = _views(http_client, database_id)["views"][0]
    assert view["settings"]["frozenColumns"] == 1


def test_fields_left_out_are_not_touched(http_client):
    database_id = _database(http_client)
    view_id = _views(http_client, database_id)["views"][0]["id"]
    _patch(http_client, database_id, view_id, name="View A", description="  Line 1\r\nLine 2  ")

    view = _patch(http_client, database_id, view_id, sorts=[]).json()

    assert view["name"] == "View A"
    assert view["description"] == "Line 1\nLine 2"


def test_description_can_be_cleared(http_client):
    database_id = _database(http_client)
    view_id = _views(http_client, database_id)["views"][0]["id"]
    _patch(http_client, database_id, view_id, description="Text")
    assert _patch(http_client, database_id, view_id, description=None).json()["description"] is None


def test_name_cannot_be_null(http_client):
    database_id = _database(http_client)
    view_id = _views(http_client, database_id)["views"][0]["id"]
    assert _patch(http_client, database_id, view_id, name=None).status_code == 422


def test_view_of_another_database_answers_404(http_client):
    first = _database(http_client)
    second = _database(http_client)
    foreign = _views(http_client, second)["views"][0]["id"]
    assert _patch(http_client, first, foreign, name="X").status_code == 404


# ─── Selection per placement ─────────────────────────────────────────────────


def _linked(client, database_id) -> str:
    resp = client.post("/api/blocks", json={
        "type": "linked_database", "parent_id": str(WORKSPACE_ROOT_ID), "reference_id": database_id,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_each_placement_keeps_its_own_selection(http_client):
    database_id = _database(http_client)
    linked_id = _linked(http_client, database_id)
    first = _views(http_client, database_id)["views"][0]["id"]
    second = http_client.post(f"/api/databases/{database_id}/views", json={}).json()["id"]

    resp = http_client.put(
        f"/api/databases/{database_id}/views/selection",
        json={"placement_id": linked_id, "view_id": second},
    )

    assert resp.status_code == 204
    assert _views(http_client, database_id, linked_id)["selected_view_id"] == second
    assert _views(http_client, database_id)["selected_view_id"] == first


def test_a_block_that_does_not_show_the_database_is_no_placement(http_client):
    database_id = _database(http_client)
    other = _database(http_client)
    view_id = _views(http_client, database_id)["views"][0]["id"]
    resp = http_client.put(
        f"/api/databases/{database_id}/views/selection",
        json={"placement_id": other, "view_id": view_id},
    )
    assert resp.status_code == 422


# ─── Pages ───────────────────────────────────────────────────────────────────


def test_page_applies_the_views_filters_and_sorts(http_client):
    database_id = _database(http_client)
    status = _schema(http_client, database_id, "Status")
    for title, state in (("Entry A", "open"), ("Entry B", "done"), ("Entry C", "open")):
        _value(http_client, database_id, _entry(http_client, database_id, title), status["id"], {"text": state})
    view_id = _views(http_client, database_id)["views"][0]["id"]
    _patch(
        http_client, database_id, view_id,
        filter_groups=[{"conjunction": "and", "filters": [
            {"schemaId": status["id"], "operator": "eq", "value": "open"},
        ]}],
        sorts=[{"schemaId": "__name__", "direction": "desc"}],
    )

    page = _page(http_client, database_id, view_id)

    assert _titles(page) == ["Entry C", "Entry A"]
    assert page["total"] == 2


def test_page_is_paged_by_offset(http_client):
    database_id = _database(http_client)
    for title in ("Entry A", "Entry B", "Entry C"):
        _entry(http_client, database_id, title)
    view_id = _views(http_client, database_id)["views"][0]["id"]
    _patch(http_client, database_id, view_id, sorts=[{"schemaId": "__name__", "direction": "asc"}])

    page = _page(http_client, database_id, view_id, offset=1, limit=1)

    assert _titles(page) == ["Entry B"]
    assert page["total"] == 3
    assert page["offset"] == 1


def test_search_narrows_the_page_without_being_stored(http_client):
    database_id = _database(http_client)
    for title in ("Alpha", "Beta"):
        _entry(http_client, database_id, title)
    view_id = _views(http_client, database_id)["views"][0]["id"]

    page = _page(http_client, database_id, view_id, search="alp")

    assert _titles(page) == ["Alpha"]
    assert _views(http_client, database_id)["views"][0]["filter_groups"] == []


def test_calendar_views_are_never_paged(http_client):
    database_id = _database(http_client)
    for title in ("Entry A", "Entry B", "Entry C"):
        _entry(http_client, database_id, title)
    view_id = _views(http_client, database_id)["views"][0]["id"]
    _patch(http_client, database_id, view_id, view_type="calendar")

    page = _page(http_client, database_id, view_id, offset=2, limit=1)

    assert len(page["entries"]) == 3
    assert page["offset"] == 0


def test_page_carries_resolved_relations_and_key_values(http_client):
    source = _database(http_client)
    target = _database(http_client)
    when = _schema(http_client, target, "When", "date")
    linked = _entry(http_client, target, "Entry B")
    _value(http_client, target, linked, when["id"], {"start": "2000-07-28"})
    link = _schema(http_client, source, "Link", "relation", config={
        "target_database_id": target,
        "keying": {"enabled": True, "key_property_id": when["id"]},
    })
    holder = _entry(http_client, source, "Entry A")
    _value(http_client, source, holder, link["id"], {"related_ids": [linked]})
    view_id = _views(http_client, source)["views"][0]["id"]

    page = _page(http_client, source, view_id)

    assert page["relations"][linked]["title"] == "Entry B"
    assert page["relations"][linked]["database_id"] == target
    assert page["key_values"][when["id"]][linked] == {"start": "2000-07-28"}


def test_filter_on_a_formula_uses_the_type_it_produces(http_client):
    """
    The frontend used to guess a formula's result type from the rows it had
    loaded. The backend now reads it from the stored results, so a numeric
    comparison works without the client sending the type.
    """
    database_id = _database(http_client)
    amount = _schema(http_client, database_id, "Amount", "number")
    double = _schema(http_client, database_id, "Double", "formula",
                     config={"expression": 'prop("Amount") * 2'})
    for title, number in (("Entry A", 2), ("Entry B", 20)):
        _value(http_client, database_id, _entry(http_client, database_id, title),
               amount["id"], {"number": number})
    view_id = _views(http_client, database_id)["views"][0]["id"]
    _patch(http_client, database_id, view_id, filter_groups=[{"conjunction": "and", "filters": [
        {"schemaId": double["id"], "operator": "gt", "value": "9"},
    ]}])

    page = _page(http_client, database_id, view_id)

    assert _titles(page) == ["Entry B"]


# ─── Property lifecycle ──────────────────────────────────────────────────────


def test_new_property_is_hidden_except_in_the_view_it_came_from(http_client):
    database_id = _database(http_client)
    first = _views(http_client, database_id)["views"][0]["id"]
    second = http_client.post(f"/api/databases/{database_id}/views", json={}).json()["id"]

    created = _schema(http_client, database_id, "Notes", visible_in_view_id=first)

    by_id = {v["id"]: v for v in _views(http_client, database_id)["views"]}
    assert created["id"] not in by_id[first]["settings"]["hiddenColumns"]
    assert created["id"] in by_id[second]["settings"]["hiddenColumns"]


def test_deleted_property_disappears_from_every_view(http_client):
    database_id = _database(http_client)
    status = _schema(http_client, database_id, "Status")
    view_id = _views(http_client, database_id)["views"][0]["id"]
    _patch(
        http_client, database_id, view_id,
        filter_groups=[{"conjunction": "and", "filters": [
            {"schemaId": status["id"], "operator": "eq", "value": "x"},
        ]}],
        sorts=[{"schemaId": status["id"], "direction": "asc"}],
        settings={
            "colWidths": {status["id"]: 120},
            "groupBySchemaId": status["id"],
            "wrapColumns": [status["id"]],
        },
    )

    assert http_client.delete(f"/api/databases/{database_id}/schemas/{status['id']}").status_code == 204

    view = _views(http_client, database_id)["views"][0]
    assert view["filter_groups"] == []
    assert view["sorts"] == []
    assert status["id"] not in view["settings"]["colOrder"]
    assert status["id"] not in view["settings"]["hiddenColumns"]
    assert view["settings"]["colWidths"] == {}
    assert "groupBySchemaId" not in view["settings"]
    assert view["settings"]["wrapColumns"] == []


def test_deleting_a_database_removes_its_views(http_client):
    database_id = _database(http_client)
    _views(http_client, database_id)
    http_client.delete(f"/api/blocks/{database_id}")
    assert http_client.delete(f"/api/blocks/{database_id}/purge").status_code == 204
    with db_module.SessionLocal() as db:
        remaining = db.query(DatabaseView).filter(DatabaseView.database_id == uuid.UUID(database_id))
        assert remaining.count() == 0


# ─── Access ──────────────────────────────────────────────────────────────────


@pytest.fixture
def private_database(http_client):
    """
    A private database owned by someone else, with its default view.

    Returns ``(database_id, view_id)``. Everything is set up as admin before a
    member client takes over the authentication overrides.
    """
    database_id = _database(http_client)
    view_id = _views(http_client, database_id)["views"][0]["id"]
    with db_module.SessionLocal() as db:
        perm_repo.set_permission(db, uuid.UUID(database_id), "private", [])
        db.commit()
    return database_id, view_id


@pytest.fixture
def member_client(private_database):
    """A client authenticated as a member without any grants."""
    with db_module.SessionLocal() as db:
        member = User(
            id=uuid.uuid4(), username=f"member_{uuid.uuid4().hex[:8]}",
            password_hash="x", role="member", is_active=True,
        )
        db.add(member)
        db.commit()
        db.refresh(member)
        db.expunge(member)
    app.dependency_overrides[require_session] = lambda: member.id
    app.dependency_overrides[get_current_user] = lambda: member
    with TestClient(app) as client:
        yield client


def test_a_member_without_access_cannot_read_views(private_database, member_client):
    database_id, _ = private_database
    assert member_client.get(f"/api/databases/{database_id}/views").status_code == 404


def test_a_member_without_access_cannot_change_views(private_database, member_client):
    database_id, view_id = private_database
    resp = member_client.patch(f"/api/databases/{database_id}/views/{view_id}", json={"name": "X"})
    assert resp.status_code == 403


def test_a_member_without_access_cannot_create_views(private_database, member_client):
    database_id, _ = private_database
    assert member_client.post(f"/api/databases/{database_id}/views", json={}).status_code == 403


def test_a_member_without_access_cannot_read_pages(private_database, member_client):
    database_id, view_id = private_database
    resp = member_client.post(f"/api/databases/{database_id}/views/{view_id}/entries", json={})
    assert resp.status_code == 404


# ─── Read access ─────────────────────────────────────────────────────────────


@pytest.fixture
def reader_client(private_database):
    """A client authenticated as a member with read access to the database."""
    database_id, _ = private_database
    with db_module.SessionLocal() as db:
        reader = User(
            id=uuid.uuid4(), username=f"member_{uuid.uuid4().hex[:8]}",
            password_hash="x", role="member", is_active=True,
        )
        db.add(reader)
        db.flush()
        perm_repo.set_permission(db, uuid.UUID(database_id), "whitelist", {reader.id: "read"})
        db.commit()
        db.refresh(reader)
        db.expunge(reader)
    app.dependency_overrides[require_session] = lambda: reader.id
    app.dependency_overrides[get_current_user] = lambda: reader
    with TestClient(app) as client:
        yield client


def test_a_reader_may_read_views_and_pages(private_database, reader_client):
    database_id, view_id = private_database
    assert reader_client.get(f"/api/databases/{database_id}/views").status_code == 200
    resp = reader_client.post(f"/api/databases/{database_id}/views/{view_id}/entries", json={})
    assert resp.status_code == 200


def test_a_reader_may_choose_the_shown_view(private_database, reader_client):
    database_id, view_id = private_database
    resp = reader_client.put(
        f"/api/databases/{database_id}/views/selection",
        json={"placement_id": database_id, "view_id": view_id},
    )
    assert resp.status_code == 204


@pytest.mark.parametrize(
    "method,suffix,body",
    [
        ("POST", "/views", {}),
        ("POST", "/views/{view}/duplicate", {}),
        ("PATCH", "/views/{view}", {"name": "View B"}),
        ("DELETE", "/views/{view}", None),
        ("PUT", "/views/order", {"view_ids": ["{view}"]}),
    ],
)
def test_a_reader_cannot_change_views(private_database, reader_client, method, suffix, body):
    from app.session.deps import READ_ONLY_DETAIL

    database_id, view_id = private_database
    url = f"/api/databases/{database_id}" + suffix.replace("{view}", view_id)
    if body is not None and "view_ids" in body:
        body = {"view_ids": [view_id]}
    kwargs = {"json": body} if body is not None else {}
    resp = reader_client.request(method, url, **kwargs)
    assert resp.status_code == 403
    assert resp.json()["detail"] == READ_ONLY_DETAIL
