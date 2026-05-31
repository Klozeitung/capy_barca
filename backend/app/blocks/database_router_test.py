"""
Tests for the database router.

All tests run against the isolated in-memory SQLite database provided by
the autouse ``isolated_db`` fixture in conftest.py, and exercise the HTTP
layer via the ``http_client`` fixture defined there.
"""
import uuid

import pytest

from app.blocks.models import WORKSPACE_ROOT_ID


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _create_database(http_client, title: str | None = None) -> str:
    """Create a database block and return its id string."""
    content = {"title": title} if title else None
    body = {"type": "database", "parent_id": str(WORKSPACE_ROOT_ID)}
    if content:
        body["content"] = content
    resp = http_client.post("/api/blocks", json=body)
    assert resp.status_code == 201
    return resp.json()["id"]


def _create_schema(
    http_client,
    database_id: str,
    name: str = "Status",
    type_: str = "select",
    config: dict | None = None,
    group: str | None = None,
) -> dict:
    body: dict = {"name": name, "type": type_}
    if config is not None:
        body["config"] = config
    if group is not None:
        body["group"] = group
    resp = http_client.post(f"/api/databases/{database_id}/schemas", json=body)
    assert resp.status_code == 201
    return resp.json()


def _create_entry(http_client, database_id: str) -> dict:
    resp = http_client.post(f"/api/databases/{database_id}/entries")
    assert resp.status_code == 201
    return resp.json()


def _upsert_value(http_client, database_id, entry_id, schema_id, value):
    resp = http_client.put(
        f"/api/databases/{database_id}/entries/{entry_id}/values/{schema_id}",
        json={"value": value},
    )
    assert resp.status_code == 204
    return resp


# ─── GET /api/databases ───────────────────────────────────────────────────────


def test_list_databases_returns_200(http_client):
    response = http_client.get("/api/databases")
    assert response.status_code == 200


def test_list_databases_returns_empty_when_none_exist(http_client):
    result = http_client.get("/api/databases").json()
    assert result == []


def test_list_databases_returns_created_database(http_client):
    _create_database(http_client)
    result = http_client.get("/api/databases").json()
    assert len(result) == 1
    assert "id" in result[0]
    assert "title" in result[0]


def test_list_databases_title_is_null_when_no_content(http_client):
    _create_database(http_client)
    result = http_client.get("/api/databases").json()
    assert result[0]["title"] is None


def test_list_databases_does_not_include_page_blocks(http_client):
    # Create a page and a database; only database should appear.
    http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    _create_database(http_client)
    result = http_client.get("/api/databases").json()
    assert len(result) == 1


def test_list_databases_returns_multiple_databases(http_client):
    _create_database(http_client)
    _create_database(http_client)
    result = http_client.get("/api/databases").json()
    assert len(result) == 2


# ─── GET /api/databases/{id}/schemas ─────────────────────────────────────────


def test_list_schemas_returns_200(http_client):
    db_id = _create_database(http_client)
    response = http_client.get(f"/api/databases/{db_id}/schemas")
    assert response.status_code == 200


def test_list_schemas_returns_empty_for_new_database(http_client):
    db_id = _create_database(http_client)
    schemas = http_client.get(f"/api/databases/{db_id}/schemas").json()
    assert schemas == []


def test_list_schemas_returns_created_schema(http_client):
    db_id = _create_database(http_client)
    _create_schema(http_client, db_id, name="Priority")
    schemas = http_client.get(f"/api/databases/{db_id}/schemas").json()
    assert any(s["name"] == "Priority" for s in schemas)


def test_list_schemas_ordered_by_position(http_client):
    db_id = _create_database(http_client)
    http_client.post(
        f"/api/databases/{db_id}/schemas",
        json={"name": "Z", "type": "text", "position": 3.0},
    )
    http_client.post(
        f"/api/databases/{db_id}/schemas",
        json={"name": "A", "type": "text", "position": 1.0},
    )
    schemas = http_client.get(f"/api/databases/{db_id}/schemas").json()
    positions = [s["position"] for s in schemas]
    assert positions == sorted(positions)


def test_list_schemas_unknown_database_returns_404(http_client):
    response = http_client.get(f"/api/databases/{uuid.uuid4()}/schemas")
    assert response.status_code == 404


def test_list_schemas_non_database_block_returns_409(http_client):
    resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    page_id = resp.json()["id"]
    response = http_client.get(f"/api/databases/{page_id}/schemas")
    assert response.status_code == 409


# ─── POST /api/databases/{id}/schemas ────────────────────────────────────────


def test_create_schema_returns_201(http_client):
    db_id = _create_database(http_client)
    response = http_client.post(
        f"/api/databases/{db_id}/schemas",
        json={"name": "Status", "type": "select"},
    )
    assert response.status_code == 201


def test_create_schema_response_contains_id(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id)
    assert "id" in schema
    assert uuid.UUID(schema["id"])


def test_create_schema_stores_correct_fields(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id, name="Due", type_="date")
    assert schema["name"] == "Due"
    assert schema["type"] == "date"
    assert schema["database_id"] == db_id


def test_create_schema_auto_assigns_position(http_client):
    db_id = _create_database(http_client)
    s1 = _create_schema(http_client, db_id, name="A")
    s2 = _create_schema(http_client, db_id, name="B")
    assert s2["position"] > s1["position"]


def test_create_schema_with_config(http_client):
    db_id = _create_database(http_client)
    resp = http_client.post(
        f"/api/databases/{db_id}/schemas",
        json={"name": "Status", "type": "select", "config": {"options": ["Todo", "Done"]}},
    )
    assert resp.json()["config"] == {"options": ["Todo", "Done"]}


def test_create_schema_duplicate_name_returns_409(http_client):
    db_id = _create_database(http_client)
    _create_schema(http_client, db_id, name="Status")
    response = http_client.post(
        f"/api/databases/{db_id}/schemas",
        json={"name": "Status", "type": "text"},
    )
    assert response.status_code == 409


def test_create_schema_same_name_different_database_is_allowed(http_client):
    db_a = _create_database(http_client)
    db_b = _create_database(http_client)
    _create_schema(http_client, db_a, name="Status")
    response = http_client.post(
        f"/api/databases/{db_b}/schemas",
        json={"name": "Status", "type": "text"},
    )
    assert response.status_code == 201


def test_create_schema_unknown_database_returns_404(http_client):
    response = http_client.post(
        f"/api/databases/{uuid.uuid4()}/schemas",
        json={"name": "X", "type": "text"},
    )
    assert response.status_code == 404


# ─── PATCH /api/databases/{id}/schemas/{schema_id} ───────────────────────────


def test_update_schema_returns_200(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id)
    response = http_client.patch(
        f"/api/databases/{db_id}/schemas/{schema['id']}",
        json={"name": "Priority"},
    )
    assert response.status_code == 200


def test_update_schema_changes_name(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id, name="OldName")
    updated = http_client.patch(
        f"/api/databases/{db_id}/schemas/{schema['id']}",
        json={"name": "NewName"},
    ).json()
    assert updated["name"] == "NewName"


def test_update_schema_changes_config(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id)
    new_config = {"options": ["Low", "Medium", "High"]}
    updated = http_client.patch(
        f"/api/databases/{db_id}/schemas/{schema['id']}",
        json={"config": new_config},
    ).json()
    assert updated["config"] == new_config


def test_update_schema_skips_unset_fields(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id, name="Keep", type_="select")
    updated = http_client.patch(
        f"/api/databases/{db_id}/schemas/{schema['id']}",
        json={"position": 99.0},
    ).json()
    assert updated["name"] == "Keep"
    assert updated["type"] == "select"


def test_update_schema_duplicate_name_returns_409(http_client):
    db_id = _create_database(http_client)
    _create_schema(http_client, db_id, name="Existing")
    schema = _create_schema(http_client, db_id, name="Other")
    response = http_client.patch(
        f"/api/databases/{db_id}/schemas/{schema['id']}",
        json={"name": "Existing"},
    )
    assert response.status_code == 409


def test_update_schema_unknown_schema_returns_404(http_client):
    db_id = _create_database(http_client)
    response = http_client.patch(
        f"/api/databases/{db_id}/schemas/{uuid.uuid4()}",
        json={"name": "X"},
    )
    assert response.status_code == 404


def test_update_schema_wrong_database_returns_404(http_client):
    db_a = _create_database(http_client)
    db_b = _create_database(http_client)
    schema = _create_schema(http_client, db_a)
    response = http_client.patch(
        f"/api/databases/{db_b}/schemas/{schema['id']}",
        json={"name": "Hijack"},
    )
    assert response.status_code == 404


# ─── DELETE /api/databases/{id}/schemas/{schema_id} ──────────────────────────


def test_delete_schema_returns_204(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id)
    response = http_client.delete(f"/api/databases/{db_id}/schemas/{schema['id']}")
    assert response.status_code == 204


def test_delete_schema_removes_from_list(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id)
    http_client.delete(f"/api/databases/{db_id}/schemas/{schema['id']}")
    schemas = http_client.get(f"/api/databases/{db_id}/schemas").json()
    assert all(s["id"] != schema["id"] for s in schemas)


def test_delete_schema_unknown_returns_404(http_client):
    db_id = _create_database(http_client)
    response = http_client.delete(
        f"/api/databases/{db_id}/schemas/{uuid.uuid4()}"
    )
    assert response.status_code == 404


def test_delete_schema_wrong_database_returns_404(http_client):
    db_a = _create_database(http_client)
    db_b = _create_database(http_client)
    schema = _create_schema(http_client, db_a)
    response = http_client.delete(
        f"/api/databases/{db_b}/schemas/{schema['id']}"
    )
    assert response.status_code == 404


# ─── GET /api/databases/{id}/entries ─────────────────────────────────────────


def test_list_entries_returns_200(http_client):
    db_id = _create_database(http_client)
    response = http_client.get(f"/api/databases/{db_id}/entries")
    assert response.status_code == 200


def test_list_entries_empty_for_new_database(http_client):
    db_id = _create_database(http_client)
    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    assert entries == []


def test_list_entries_contains_created_entry(http_client):
    db_id = _create_database(http_client)
    entry = _create_entry(http_client, db_id)
    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    assert any(e["id"] == entry["id"] for e in entries)


def test_list_entries_contains_values_field(http_client):
    db_id = _create_database(http_client)
    _create_entry(http_client, db_id)
    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    assert "values" in entries[0]
    assert isinstance(entries[0]["values"], dict)


def test_list_entries_unknown_database_returns_404(http_client):
    response = http_client.get(f"/api/databases/{uuid.uuid4()}/entries")
    assert response.status_code == 404


# ─── POST /api/databases/{id}/entries ────────────────────────────────────────


def test_create_entry_returns_201(http_client):
    db_id = _create_database(http_client)
    response = http_client.post(f"/api/databases/{db_id}/entries")
    assert response.status_code == 201


def test_create_entry_response_has_id(http_client):
    db_id = _create_database(http_client)
    entry = _create_entry(http_client, db_id)
    assert "id" in entry
    assert uuid.UUID(entry["id"])


def test_create_entry_response_has_empty_values(http_client):
    db_id = _create_database(http_client)
    entry = _create_entry(http_client, db_id)
    assert entry["values"] == {}


def test_create_entry_unknown_database_returns_404(http_client):
    response = http_client.post(f"/api/databases/{uuid.uuid4()}/entries")
    assert response.status_code == 404


def test_create_multiple_entries_increments_position(http_client):
    db_id = _create_database(http_client)
    e1 = _create_entry(http_client, db_id)
    e2 = _create_entry(http_client, db_id)
    assert e2["position"] > e1["position"]


# ─── POST /api/databases/{id}/entries/{entry_id}/duplicate ───────────────────


def test_duplicate_entry_returns_201(http_client):
    db_id = _create_database(http_client)
    entry = _create_entry(http_client, db_id)
    response = http_client.post(f"/api/databases/{db_id}/entries/{entry['id']}/duplicate")
    assert response.status_code == 201


def test_duplicate_entry_has_new_id_and_higher_position(http_client):
    db_id = _create_database(http_client)
    entry = _create_entry(http_client, db_id)
    dup = http_client.post(
        f"/api/databases/{db_id}/entries/{entry['id']}/duplicate"
    ).json()
    assert dup["id"] != entry["id"]
    assert dup["position"] > entry["position"]


def test_duplicate_entry_copies_content(http_client):
    db_id = _create_database(http_client)
    entry = _create_entry(http_client, db_id)
    http_client.patch(
        f"/api/blocks/{entry['id']}",
        json={"content": {"title": "Original Title"}},
    )
    dup = http_client.post(
        f"/api/databases/{db_id}/entries/{entry['id']}/duplicate"
    ).json()
    assert dup["content"] == {"title": "Original Title"}


def test_duplicate_entry_copies_icon(http_client):
    db_id = _create_database(http_client)
    entry = _create_entry(http_client, db_id)
    http_client.patch(f"/api/blocks/{entry['id']}/appearance", json={"icon": "mdi:star"})
    dup = http_client.post(
        f"/api/databases/{db_id}/entries/{entry['id']}/duplicate"
    ).json()
    assert dup["icon"] == "mdi:star"


def test_duplicate_entry_copies_state(http_client):
    db_id = _create_database(http_client)
    entry = _create_entry(http_client, db_id)
    dup = http_client.post(
        f"/api/databases/{db_id}/entries/{entry['id']}/duplicate"
    ).json()
    assert dup["state"] == entry["state"]


def test_duplicate_entry_copies_property_values(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id, name="Notes", type_="text")
    entry = _create_entry(http_client, db_id)
    _upsert_value(http_client, db_id, entry["id"], schema["id"], {"text": "copied value"})
    dup = http_client.post(
        f"/api/databases/{db_id}/entries/{entry['id']}/duplicate"
    ).json()
    assert dup["values"][schema["id"]] == {"text": "copied value"}


def test_duplicate_entry_copies_multiple_property_values(http_client):
    db_id = _create_database(http_client)
    s1 = _create_schema(http_client, db_id, name="Name", type_="text")
    s2 = _create_schema(http_client, db_id, name="Done", type_="checkbox")
    entry = _create_entry(http_client, db_id)
    _upsert_value(http_client, db_id, entry["id"], s1["id"], {"text": "hello"})
    _upsert_value(http_client, db_id, entry["id"], s2["id"], {"checked": True})
    dup = http_client.post(
        f"/api/databases/{db_id}/entries/{entry['id']}/duplicate"
    ).json()
    assert dup["values"][s1["id"]] == {"text": "hello"}
    assert dup["values"][s2["id"]] == {"checked": True}


def test_duplicate_entry_gets_fresh_readonly_values(http_client):
    """Readonly properties (id, created_*) must differ between original and duplicate."""
    db_id = _create_database(http_client)
    http_client.post(f"/api/databases/{db_id}/seed-readonly-schemas")
    entry = _create_entry(http_client, db_id)
    schemas = http_client.get(f"/api/databases/{db_id}/schemas").json()
    id_schema = next(s for s in schemas if s["type"] == "id")

    dup = http_client.post(
        f"/api/databases/{db_id}/entries/{entry['id']}/duplicate"
    ).json()

    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    orig_row = next(e for e in entries if e["id"] == entry["id"])
    orig_id_val = (orig_row["values"].get(id_schema["id"]) or {}).get("id_value")
    dup_id_val = (dup["values"].get(id_schema["id"]) or {}).get("id_value")
    assert dup_id_val is not None
    assert dup_id_val != orig_id_val


def test_duplicate_entry_unknown_database_returns_404(http_client):
    response = http_client.post(
        f"/api/databases/{uuid.uuid4()}/entries/{uuid.uuid4()}/duplicate"
    )
    assert response.status_code == 404


def test_duplicate_entry_unknown_entry_returns_404(http_client):
    db_id = _create_database(http_client)
    response = http_client.post(
        f"/api/databases/{db_id}/entries/{uuid.uuid4()}/duplicate"
    )
    assert response.status_code == 404


def test_duplicate_entry_entry_from_different_database_returns_404(http_client):
    db_a = _create_database(http_client)
    db_b = _create_database(http_client)
    entry = _create_entry(http_client, db_b)
    response = http_client.post(
        f"/api/databases/{db_a}/entries/{entry['id']}/duplicate"
    )
    assert response.status_code == 404


# ─── PUT /api/databases/{id}/entries/{entry_id}/values/{schema_id} ───────────


def test_upsert_value_returns_204(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id)
    entry = _create_entry(http_client, db_id)
    response = http_client.put(
        f"/api/databases/{db_id}/entries/{entry['id']}/values/{schema['id']}",
        json={"value": {"text": "Hello"}},
    )
    assert response.status_code == 204


def test_upsert_value_appears_in_entries_list(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id)
    entry = _create_entry(http_client, db_id)
    http_client.put(
        f"/api/databases/{db_id}/entries/{entry['id']}/values/{schema['id']}",
        json={"value": {"text": "My value"}},
    )
    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    row = next(e for e in entries if e["id"] == entry["id"])
    assert row["values"][schema["id"]] == {"text": "My value"}


def test_upsert_value_null_clears_cell(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id)
    entry = _create_entry(http_client, db_id)
    http_client.put(
        f"/api/databases/{db_id}/entries/{entry['id']}/values/{schema['id']}",
        json={"value": {"text": "X"}},
    )
    http_client.put(
        f"/api/databases/{db_id}/entries/{entry['id']}/values/{schema['id']}",
        json={"value": None},
    )
    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    row = next(e for e in entries if e["id"] == entry["id"])
    assert row["values"][schema["id"]] is None


def test_upsert_value_unknown_entry_returns_404(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id)
    response = http_client.put(
        f"/api/databases/{db_id}/entries/{uuid.uuid4()}/values/{schema['id']}",
        json={"value": {"text": "X"}},
    )
    assert response.status_code == 404


def test_upsert_value_unknown_schema_returns_404(http_client):
    db_id = _create_database(http_client)
    entry = _create_entry(http_client, db_id)
    response = http_client.put(
        f"/api/databases/{db_id}/entries/{entry['id']}/values/{uuid.uuid4()}",
        json={"value": {"text": "X"}},
    )
    assert response.status_code == 404


def test_upsert_value_schema_from_other_database_returns_404(http_client):
    db_a = _create_database(http_client)
    db_b = _create_database(http_client)
    schema_b = _create_schema(http_client, db_b)
    entry_a = _create_entry(http_client, db_a)
    response = http_client.put(
        f"/api/databases/{db_a}/entries/{entry_a['id']}/values/{schema_b['id']}",
        json={"value": {"text": "X"}},
    )
    assert response.status_code == 404


# ─── Relation property: bilateral sync ───────────────────────────────────────


def _create_relation_schema(http_client, database_id: str, target_db_id: str,
                             name: str = "Links", direction: str = "unilateral",
                             mirror_name: str | None = None) -> dict:
    config = {
        "target_database_id": target_db_id,
        "direction": direction,
        "mirror_property_name": mirror_name,
    }
    return _create_schema(http_client, database_id, name=name, type_="relation", config=config)


def test_upsert_unilateral_relation_stores_value(http_client):
    db_a = _create_database(http_client)
    db_b = _create_database(http_client)
    schema = _create_relation_schema(http_client, db_a, db_b, direction="unilateral")
    entry_a = _create_entry(http_client, db_a)
    entry_b = _create_entry(http_client, db_b)

    _upsert_value(http_client, db_a, entry_a["id"], schema["id"],
                  {"related_ids": [entry_b["id"]]})

    entries = http_client.get(f"/api/databases/{db_a}/entries").json()
    row = next(e for e in entries if e["id"] == entry_a["id"])
    assert row["values"][schema["id"]] == {"related_ids": [entry_b["id"]]}


def test_upsert_bilateral_relation_creates_mirror_schema(http_client):
    db_a = _create_database(http_client)
    db_b = _create_database(http_client)
    schema_a = _create_relation_schema(
        http_client, db_a, db_b,
        name="From A", direction="bilateral", mirror_name="From B",
    )
    entry_a = _create_entry(http_client, db_a)
    entry_b = _create_entry(http_client, db_b)

    _upsert_value(http_client, db_a, entry_a["id"], schema_a["id"],
                  {"related_ids": [entry_b["id"]]})

    # Mirror schema must now exist in db_b.
    schemas_b = http_client.get(f"/api/databases/{db_b}/schemas").json()
    assert any(s["name"] == "From B" and s["type"] == "relation" for s in schemas_b)


def test_upsert_bilateral_relation_writes_mirror_value(http_client):
    db_a = _create_database(http_client)
    db_b = _create_database(http_client)
    schema_a = _create_relation_schema(
        http_client, db_a, db_b,
        name="From A", direction="bilateral", mirror_name="From B",
    )
    entry_a = _create_entry(http_client, db_a)
    entry_b = _create_entry(http_client, db_b)

    _upsert_value(http_client, db_a, entry_a["id"], schema_a["id"],
                  {"related_ids": [entry_b["id"]]})

    # entry_b in db_b must now point back to entry_a.
    entries_b = http_client.get(f"/api/databases/{db_b}/entries").json()
    mirror_schema_id = next(
        s["id"]
        for s in http_client.get(f"/api/databases/{db_b}/schemas").json()
        if s["name"] == "From B"
    )
    row_b = next(e for e in entries_b if e["id"] == entry_b["id"])
    assert entry_a["id"] in row_b["values"].get(mirror_schema_id, {}).get("related_ids", [])


def test_upsert_bilateral_relation_removes_mirror_value_on_unlink(http_client):
    db_a = _create_database(http_client)
    db_b = _create_database(http_client)
    schema_a = _create_relation_schema(
        http_client, db_a, db_b,
        name="From A", direction="bilateral", mirror_name="From B",
    )
    entry_a = _create_entry(http_client, db_a)
    entry_b = _create_entry(http_client, db_b)

    # Link
    _upsert_value(http_client, db_a, entry_a["id"], schema_a["id"],
                  {"related_ids": [entry_b["id"]]})
    # Unlink
    _upsert_value(http_client, db_a, entry_a["id"], schema_a["id"],
                  {"related_ids": []})

    entries_b = http_client.get(f"/api/databases/{db_b}/entries").json()
    mirror_schema_id = next(
        s["id"]
        for s in http_client.get(f"/api/databases/{db_b}/schemas").json()
        if s["name"] == "From B"
    )
    row_b = next(e for e in entries_b if e["id"] == entry_b["id"])
    mirror_val = row_b["values"].get(mirror_schema_id)
    related = (mirror_val or {}).get("related_ids", [])
    assert entry_a["id"] not in related


def test_upsert_bilateral_relation_reuses_existing_mirror_schema(http_client):
    """Second write must not create a duplicate mirror schema."""
    db_a = _create_database(http_client)
    db_b = _create_database(http_client)
    schema_a = _create_relation_schema(
        http_client, db_a, db_b,
        name="From A", direction="bilateral", mirror_name="From B",
    )
    entry_a = _create_entry(http_client, db_a)
    entry_b1 = _create_entry(http_client, db_b)
    entry_b2 = _create_entry(http_client, db_b)

    _upsert_value(http_client, db_a, entry_a["id"], schema_a["id"],
                  {"related_ids": [entry_b1["id"]]})
    _upsert_value(http_client, db_a, entry_a["id"], schema_a["id"],
                  {"related_ids": [entry_b1["id"], entry_b2["id"]]})

    schemas_b = http_client.get(f"/api/databases/{db_b}/schemas").json()
    mirror_count = sum(1 for s in schemas_b if s["name"] == "From B")
    assert mirror_count == 1


def test_upsert_self_referential_bilateral_relation(http_client):
    """A database may relate to itself (self-referential relation)."""
    db_a = _create_database(http_client)
    schema = _create_relation_schema(
        http_client, db_a, db_a,
        name="Related", direction="bilateral", mirror_name="Related",
    )
    entry_1 = _create_entry(http_client, db_a)
    entry_2 = _create_entry(http_client, db_a)

    _upsert_value(http_client, db_a, entry_1["id"], schema["id"],
                  {"related_ids": [entry_2["id"]]})

    entries = http_client.get(f"/api/databases/{db_a}/entries").json()
    row_2 = next(e for e in entries if e["id"] == entry_2["id"])
    related = (row_2["values"].get(schema["id"]) or {}).get("related_ids", [])
    assert entry_1["id"] in related


def test_upsert_bilateral_self_relation_writes_reverse_link(http_client):
    """bilateral_self: linking A→B automatically links B→A in the same schema."""
    db_a = _create_database(http_client)
    schema = _create_relation_schema(
        http_client, db_a, db_a,
        name="Siblings", direction="bilateral_self",
    )
    entry_1 = _create_entry(http_client, db_a)
    entry_2 = _create_entry(http_client, db_a)

    _upsert_value(http_client, db_a, entry_1["id"], schema["id"],
                  {"related_ids": [entry_2["id"]]})

    entries = http_client.get(f"/api/databases/{db_a}/entries").json()
    row_2 = next(e for e in entries if e["id"] == entry_2["id"])
    related = (row_2["values"].get(schema["id"]) or {}).get("related_ids", [])
    assert entry_1["id"] in related


def test_upsert_bilateral_self_relation_removes_reverse_link_on_unlink(http_client):
    """bilateral_self: removing A→B also removes B→A."""
    db_a = _create_database(http_client)
    schema = _create_relation_schema(
        http_client, db_a, db_a,
        name="Siblings", direction="bilateral_self",
    )
    entry_1 = _create_entry(http_client, db_a)
    entry_2 = _create_entry(http_client, db_a)

    _upsert_value(http_client, db_a, entry_1["id"], schema["id"],
                  {"related_ids": [entry_2["id"]]})
    _upsert_value(http_client, db_a, entry_1["id"], schema["id"],
                  {"related_ids": []})

    entries = http_client.get(f"/api/databases/{db_a}/entries").json()
    row_2 = next(e for e in entries if e["id"] == entry_2["id"])
    related = (row_2["values"].get(schema["id"]) or {}).get("related_ids", [])
    assert entry_1["id"] not in related


def test_bilateral_self_does_not_create_mirror_schema(http_client):
    """bilateral_self must not create a second schema — it mirrors itself."""
    db_a = _create_database(http_client)
    _create_relation_schema(
        http_client, db_a, db_a,
        name="Siblings", direction="bilateral_self",
    )
    schemas = http_client.get(f"/api/databases/{db_a}/schemas").json()
    relation_schemas = [s for s in schemas if s["type"] == "relation"]
    assert len(relation_schemas) == 1, (
        "bilateral_self must not create a separate mirror schema"
    )


def test_bilateral_self_relation_multiple_entries(http_client):
    """bilateral_self: A linked to B and C → both B and C link back to A."""
    db_a = _create_database(http_client)
    schema = _create_relation_schema(
        http_client, db_a, db_a,
        name="Siblings", direction="bilateral_self",
    )
    entry_1 = _create_entry(http_client, db_a)
    entry_2 = _create_entry(http_client, db_a)
    entry_3 = _create_entry(http_client, db_a)

    _upsert_value(http_client, db_a, entry_1["id"], schema["id"],
                  {"related_ids": [entry_2["id"], entry_3["id"]]})

    entries = http_client.get(f"/api/databases/{db_a}/entries").json()
    for eid in (entry_2["id"], entry_3["id"]):
        row = next(e for e in entries if e["id"] == eid)
        related = (row["values"].get(schema["id"]) or {}).get("related_ids", [])
        assert entry_1["id"] in related



    """Mirror schema must exist right after create_schema — no upsert needed."""
    db_a = _create_database(http_client)
    db_b = _create_database(http_client)
    _create_relation_schema(
        http_client, db_a, db_b,
        name="From A", direction="bilateral", mirror_name="From B",
    )

    # Mirror schema must already exist in db_b without any entry having been linked.
    schemas_b = http_client.get(f"/api/databases/{db_b}/schemas").json()
    assert any(s["name"] == "From B" and s["type"] == "relation" for s in schemas_b), (
        "Mirror schema 'From B' should be created eagerly when the bilateral "
        "relation schema is first saved, not lazily on first upsert."
    )


def test_update_bilateral_mirror_name_renames_mirror_schema(http_client):
    """Changing mirror_property_name must rename the existing mirror schema, not create a new one."""
    db_a = _create_database(http_client)
    db_b = _create_database(http_client)
    schema_a = _create_relation_schema(
        http_client, db_a, db_b,
        name="From A", direction="bilateral", mirror_name="From B",
    )

    # Rename the mirror property name.
    updated = http_client.patch(
        f"/api/databases/{db_a}/schemas/{schema_a['id']}",
        json={
            "config": {
                "target_database_id": db_b,
                "direction": "bilateral",
                "mirror_property_name": "From B (renamed)",
            }
        },
    )
    assert updated.status_code == 200

    schemas_b = http_client.get(f"/api/databases/{db_b}/schemas").json()
    names = [s["name"] for s in schemas_b if s["type"] == "relation"]

    assert "From B (renamed)" in names, "Mirror schema should be renamed to 'From B (renamed)'"
    assert "From B" not in names, "Old mirror schema name 'From B' should no longer exist"
    # No duplicate must have been created.
    assert names.count("From B (renamed)") == 1


def test_update_bilateral_source_name_updates_mirror_back_pointer(http_client):
    """Renaming the source schema must update the mirror's back-pointer config."""
    db_a = _create_database(http_client)
    db_b = _create_database(http_client)
    schema_a = _create_relation_schema(
        http_client, db_a, db_b,
        name="From A", direction="bilateral", mirror_name="From B",
    )

    # Rename the source schema itself.
    updated = http_client.patch(
        f"/api/databases/{db_a}/schemas/{schema_a['id']}",
        json={"name": "From A v2"},
    )
    assert updated.status_code == 200

    # The mirror schema's config.mirror_property_name must now point to "From A v2".
    schemas_b = http_client.get(f"/api/databases/{db_b}/schemas").json()
    mirror = next((s for s in schemas_b if s["name"] == "From B"), None)
    assert mirror is not None, "Mirror schema 'From B' should still exist"
    assert (mirror.get("config") or {}).get("mirror_property_name") == "From A v2", (
        "Mirror schema's mirror_property_name should point to the renamed source 'From A v2'"
    )


# ─── New property types: basic formatted ─────────────────────────────────────


def test_create_email_schema(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id, name="Email", type_="email")
    assert schema["type"] == "email"


def test_upsert_email_value(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id, name="Email", type_="email")
    entry = _create_entry(http_client, db_id)
    _upsert_value(http_client, db_id, entry["id"], schema["id"], {"value": "test@example.com"})
    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    row = next(e for e in entries if e["id"] == entry["id"])
    assert row["values"][schema["id"]] == {"value": "test@example.com"}


def test_create_phone_schema(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id, name="Phone", type_="phone")
    assert schema["type"] == "phone"


def test_create_url_schema(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id, name="Website", type_="url")
    assert schema["type"] == "url"


# ─── New property types: file upload ─────────────────────────────────────────


def test_create_file_schema(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id, name="Attachments", type_="file")
    assert schema["type"] == "file"


# ─── New property types: date with time / end date ───────────────────────────


def test_create_date_schema_with_config(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(
        http_client, db_id, name="Period", type_="date",
        config={"includeTime": False, "hasEndDate": True},
    )
    assert schema["config"]["hasEndDate"] is True


def test_upsert_date_value_start_and_end(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id, name="Period", type_="date",
                             config={"includeTime": False, "hasEndDate": True})
    entry = _create_entry(http_client, db_id)
    _upsert_value(http_client, db_id, entry["id"], schema["id"],
                  {"start": "2025-01-01", "end": "2025-01-31"})
    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    row = next(e for e in entries if e["id"] == entry["id"])
    assert row["values"][schema["id"]] == {"start": "2025-01-01", "end": "2025-01-31"}


# ─── New property types: readonly system fields ───────────────────────────────


def test_create_id_schema(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id, name="ID", type_="id",
                             config={"prefix": "PROJ-", "next_id": 1})
    assert schema["type"] == "id"
    assert schema["config"]["prefix"] == "PROJ-"
    assert schema["config"]["next_id"] == 1


def test_id_auto_populated_on_entry_creation(http_client):
    db_id = _create_database(http_client)
    _create_schema(http_client, db_id, name="ID", type_="id",
                   config={"prefix": "", "next_id": 1})
    entry = _create_entry(http_client, db_id)
    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    row = next(e for e in entries if e["id"] == entry["id"])
    schema_id = next(
        s["id"] for s in http_client.get(f"/api/databases/{db_id}/schemas").json()
        if s["type"] == "id"
    )
    assert row["values"].get(schema_id, {}) is not None
    assert row["values"][schema_id]["id_value"] == 1


def test_id_increments_on_second_entry(http_client):
    db_id = _create_database(http_client)
    _create_schema(http_client, db_id, name="ID", type_="id",
                   config={"prefix": "", "next_id": 1})
    _create_entry(http_client, db_id)
    _create_entry(http_client, db_id)
    schema_id = next(
        s["id"] for s in http_client.get(f"/api/databases/{db_id}/schemas").json()
        if s["type"] == "id"
    )
    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    id_values = sorted(e["values"].get(schema_id, {}).get("id_value", 0) for e in entries)
    assert id_values == [1, 2]


def test_id_next_id_config_incremented(http_client):
    """After entry creation, config.next_id must have been bumped by 1."""
    db_id = _create_database(http_client)
    _create_schema(http_client, db_id, name="ID", type_="id",
                   config={"prefix": "", "next_id": 5})
    _create_entry(http_client, db_id)
    schema = next(
        s for s in http_client.get(f"/api/databases/{db_id}/schemas").json()
        if s["type"] == "id"
    )
    assert schema["config"]["next_id"] == 6


def test_upsert_readonly_type_returns_422(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id, name="ID", type_="id",
                             config={"prefix": "", "next_id": 1})
    entry = _create_entry(http_client, db_id)
    response = http_client.put(
        f"/api/databases/{db_id}/entries/{entry['id']}/values/{schema['id']}",
        json={"value": {"id_value": 99}},
    )
    assert response.status_code == 422


def test_created_by_auto_populated(http_client):
    db_id = _create_database(http_client)
    _create_schema(http_client, db_id, name="Created by", type_="created_by")
    entry = _create_entry(http_client, db_id)
    schema_id = next(
        s["id"] for s in http_client.get(f"/api/databases/{db_id}/schemas").json()
        if s["type"] == "created_by"
    )
    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    row = next(e for e in entries if e["id"] == entry["id"])
    val = row["values"].get(schema_id)
    assert val is not None
    assert "user_id" in val  # migration 0009 changed from username to user_id


def test_created_time_auto_populated(http_client):
    db_id = _create_database(http_client)
    _create_schema(http_client, db_id, name="Created", type_="created_time")
    entry = _create_entry(http_client, db_id)
    schema_id = next(
        s["id"] for s in http_client.get(f"/api/databases/{db_id}/schemas").json()
        if s["type"] == "created_time"
    )
    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    row = next(e for e in entries if e["id"] == entry["id"])
    val = row["values"].get(schema_id)
    assert val is not None
    assert "datetime" in val


def test_last_edited_time_updated_on_upsert(http_client):
    db_id = _create_database(http_client)
    text_schema = _create_schema(http_client, db_id, name="Note", type_="text")
    _create_schema(http_client, db_id, name="Last edited", type_="last_edited_time")
    entry = _create_entry(http_client, db_id)
    le_schema_id = next(
        s["id"] for s in http_client.get(f"/api/databases/{db_id}/schemas").json()
        if s["type"] == "last_edited_time"
    )

    # Write a text value – this should trigger last_edited_time refresh.
    _upsert_value(http_client, db_id, entry["id"], text_schema["id"], {"text": "hello"})

    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    row = next(e for e in entries if e["id"] == entry["id"])
    val = row["values"].get(le_schema_id)
    assert val is not None
    assert "datetime" in val


# ─── POST /api/databases/{id}/seed-readonly-schemas ──────────────────────────


def _seed(http_client, database_id: str) -> list:
    resp = http_client.post(f"/api/databases/{database_id}/seed-readonly-schemas")
    assert resp.status_code == 200
    return resp.json()


def test_seed_creates_five_readonly_schemas(http_client):
    db_id = _create_database(http_client)
    created = _seed(http_client, db_id)
    assert len(created) == 7
    types_created = {s["type"] for s in created}
    assert types_created == {
        "id", "created_by", "created_time", "last_edited_by", "last_edited_time",
        "parent_item", "sub_item",
    }


def test_seed_is_idempotent(http_client):
    db_id = _create_database(http_client)
    _seed(http_client, db_id)
    second = _seed(http_client, db_id)
    # Second call creates nothing new.
    assert second == []


def test_seed_skips_existing_type(http_client):
    db_id = _create_database(http_client)
    _create_schema(http_client, db_id, name="My ID", type_="id",
                   config={"prefix": "", "next_id": 1})
    created = _seed(http_client, db_id)
    types = {s["type"] for s in created}
    assert "id" not in types
    assert len(created) == 6


def test_seed_schemas_appear_in_schema_list(http_client):
    db_id = _create_database(http_client)
    _seed(http_client, db_id)
    schemas = http_client.get(f"/api/databases/{db_id}/schemas").json()
    schema_types = {s["type"] for s in schemas}
    assert {"id", "created_by", "created_time", "last_edited_by", "last_edited_time",
            "parent_item", "sub_item"}.issubset(schema_types)


def test_seed_id_config_has_next_id(http_client):
    db_id = _create_database(http_client)
    _seed(http_client, db_id)
    schemas = http_client.get(f"/api/databases/{db_id}/schemas").json()
    id_schema = next(s for s in schemas if s["type"] == "id")
    assert id_schema["config"]["next_id"] == 1
    assert "prefix" in id_schema["config"]


def test_seed_unknown_database_returns_404(http_client):
    resp = http_client.post(f"/api/databases/{uuid.uuid4()}/seed-readonly-schemas")
    assert resp.status_code == 404


# ─── Sub-items (parent_item / sub_item) ──────────────────────────────────────


def _get_subitem_schemas(http_client, db_id: str) -> tuple[dict, dict]:
    """Return (parent_item_schema, sub_item_schema) after seeding."""
    _seed(http_client, db_id)
    schemas = http_client.get(f"/api/databases/{db_id}/schemas").json()
    pi = next(s for s in schemas if s["type"] == "parent_item")
    si = next(s for s in schemas if s["type"] == "sub_item")
    return pi, si


def test_seed_creates_parent_item_and_sub_item(http_client):
    db_id = _create_database(http_client)
    created = _seed(http_client, db_id)
    types = {s["type"] for s in created}
    assert "parent_item" in types
    assert "sub_item" in types


def test_seed_partner_schema_ids_match(http_client):
    """The partner_schema_id of each schema must point to the other's id."""
    db_id = _create_database(http_client)
    pi, si = _get_subitem_schemas(http_client, db_id)
    assert pi["config"]["partner_schema_id"] == si["id"]
    assert si["config"]["partner_schema_id"] == pi["id"]


def test_upsert_parent_item_syncs_sub_item(http_client):
    """Setting A's parent_item to B must add A to B's sub_item list."""
    db_id = _create_database(http_client)
    pi, si = _get_subitem_schemas(http_client, db_id)
    entry_a = _create_entry(http_client, db_id)
    entry_b = _create_entry(http_client, db_id)

    _upsert_value(http_client, db_id, entry_a["id"], pi["id"],
                  {"related_ids": [entry_b["id"]]})

    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    row_b = next(e for e in entries if e["id"] == entry_b["id"])
    sub_val = row_b["values"].get(si["id"]) or {}
    assert entry_a["id"] in (sub_val.get("related_ids") or [])


def test_upsert_parent_item_reparenting_updates_old_parent(http_client):
    """
    When A moves from parent B to parent C, B's sub_item must lose A and
    C's sub_item must gain A.
    """
    db_id = _create_database(http_client)
    pi, si = _get_subitem_schemas(http_client, db_id)
    entry_a = _create_entry(http_client, db_id)
    entry_b = _create_entry(http_client, db_id)
    entry_c = _create_entry(http_client, db_id)

    # A → parent B
    _upsert_value(http_client, db_id, entry_a["id"], pi["id"],
                  {"related_ids": [entry_b["id"]]})
    # Reparent A → parent C
    _upsert_value(http_client, db_id, entry_a["id"], pi["id"],
                  {"related_ids": [entry_c["id"]]})

    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    row_b = next(e for e in entries if e["id"] == entry_b["id"])
    row_c = next(e for e in entries if e["id"] == entry_c["id"])

    sub_b = (row_b["values"].get(si["id"]) or {}).get("related_ids") or []
    sub_c = (row_c["values"].get(si["id"]) or {}).get("related_ids") or []
    assert entry_a["id"] not in sub_b, "A must be removed from B's sub_item on reparent"
    assert entry_a["id"] in sub_c, "A must be added to C's sub_item on reparent"


def test_upsert_parent_item_clear_removes_from_parent(http_client):
    """Setting parent_item to null/empty removes the entry from the parent's sub_item."""
    db_id = _create_database(http_client)
    pi, si = _get_subitem_schemas(http_client, db_id)
    entry_a = _create_entry(http_client, db_id)
    entry_b = _create_entry(http_client, db_id)

    _upsert_value(http_client, db_id, entry_a["id"], pi["id"],
                  {"related_ids": [entry_b["id"]]})
    _upsert_value(http_client, db_id, entry_a["id"], pi["id"],
                  {"related_ids": []})

    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    row_b = next(e for e in entries if e["id"] == entry_b["id"])
    sub_val = row_b["values"].get(si["id"])
    related = (sub_val or {}).get("related_ids") or []
    assert entry_a["id"] not in related


def test_upsert_parent_item_single_parent_policy_enforced(http_client):
    """Writing more than one ID to parent_item must return 422."""
    db_id = _create_database(http_client)
    pi, _ = _get_subitem_schemas(http_client, db_id)
    entry_a = _create_entry(http_client, db_id)
    entry_b = _create_entry(http_client, db_id)
    entry_c = _create_entry(http_client, db_id)

    resp = http_client.put(
        f"/api/databases/{db_id}/entries/{entry_a['id']}/values/{pi['id']}",
        json={"value": {"related_ids": [entry_b["id"], entry_c["id"]]}},
    )
    assert resp.status_code == 422
    assert "single-parent" in resp.json()["detail"].lower()


def test_cannot_write_sub_item_directly(http_client):
    """Direct writes to sub_item must be rejected with 422."""
    db_id = _create_database(http_client)
    _, si = _get_subitem_schemas(http_client, db_id)
    entry = _create_entry(http_client, db_id)

    resp = http_client.put(
        f"/api/databases/{db_id}/entries/{entry['id']}/values/{si['id']}",
        json={"value": {"related_ids": []}},
    )
    assert resp.status_code == 422


def test_multiple_children_appear_in_sub_item(http_client):
    """Multiple entries can have the same parent; all must appear in sub_item."""
    db_id = _create_database(http_client)
    pi, si = _get_subitem_schemas(http_client, db_id)
    parent  = _create_entry(http_client, db_id)
    child_1 = _create_entry(http_client, db_id)
    child_2 = _create_entry(http_client, db_id)

    _upsert_value(http_client, db_id, child_1["id"], pi["id"],
                  {"related_ids": [parent["id"]]})
    _upsert_value(http_client, db_id, child_2["id"], pi["id"],
                  {"related_ids": [parent["id"]]})

    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    row_parent = next(e for e in entries if e["id"] == parent["id"])
    sub_val = (row_parent["values"].get(si["id"]) or {}).get("related_ids") or []
    assert child_1["id"] in sub_val
    assert child_2["id"] in sub_val


# ─── Formula property ─────────────────────────────────────────────────────────


def _create_formula_schema(http_client, database_id: str, name: str, expression: str) -> dict:
    return _create_schema(
        http_client, database_id, name=name, type_="formula",
        config={"expression": expression},
    )


def _create_number_schema(http_client, database_id: str, name: str) -> dict:
    return _create_schema(http_client, database_id, name=name, type_="number")


def test_create_formula_schema_returns_201(http_client):
    db_id = _create_database(http_client)
    schema = _create_formula_schema(http_client, db_id, "Total", "1 + 1")
    assert schema["type"] == "formula"


def test_create_formula_schema_stores_expression(http_client):
    db_id = _create_database(http_client)
    schema = _create_formula_schema(http_client, db_id, "Total", "prop('Price') * 2")
    assert schema["config"]["expression"] == "prop('Price') * 2"


def test_create_formula_schema_invalid_syntax_returns_422(http_client):
    db_id = _create_database(http_client)
    resp = http_client.post(
        f"/api/databases/{db_id}/schemas",
        json={"name": "Bad", "type": "formula", "config": {"expression": "1 +"}},
    )
    assert resp.status_code == 422
    assert "syntax" in resp.json()["detail"].lower()


def test_upsert_formula_value_directly_returns_422(http_client):
    db_id = _create_database(http_client)
    schema = _create_formula_schema(http_client, db_id, "Computed", "1 + 1")
    entry = _create_entry(http_client, db_id)
    resp = http_client.put(
        f"/api/databases/{db_id}/entries/{entry['id']}/values/{schema['id']}",
        json={"value": {"result": 99}},
    )
    assert resp.status_code == 422


def test_formula_value_computed_on_upsert(http_client):
    db_id = _create_database(http_client)
    price = _create_number_schema(http_client, db_id, "Price")
    qty = _create_number_schema(http_client, db_id, "Qty")
    _create_formula_schema(http_client, db_id, "Total", "prop('Price') * prop('Qty')")
    entry = _create_entry(http_client, db_id)

    _upsert_value(http_client, db_id, entry["id"], price["id"], {"number": 10})
    _upsert_value(http_client, db_id, entry["id"], qty["id"], {"number": 3})

    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    row = next(e for e in entries if e["id"] == entry["id"])
    total_schema = next(
        s for s in http_client.get(f"/api/databases/{db_id}/schemas").json()
        if s["type"] == "formula"
    )
    val = row["values"].get(total_schema["id"])
    assert val is not None
    assert val["result"] == pytest.approx(30.0)
    assert "error" not in val


def test_formula_recomputes_when_dependency_changes(http_client):
    db_id = _create_database(http_client)
    price = _create_number_schema(http_client, db_id, "Price")
    _create_formula_schema(http_client, db_id, "Double", "prop('Price') * 2")
    entry = _create_entry(http_client, db_id)

    _upsert_value(http_client, db_id, entry["id"], price["id"], {"number": 5})
    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    row = next(e for e in entries if e["id"] == entry["id"])
    double_schema = next(
        s for s in http_client.get(f"/api/databases/{db_id}/schemas").json()
        if s["type"] == "formula"
    )
    assert row["values"][double_schema["id"]]["result"] == pytest.approx(10.0)

    # Update price – formula must re-compute
    _upsert_value(http_client, db_id, entry["id"], price["id"], {"number": 20})
    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    row = next(e for e in entries if e["id"] == entry["id"])
    assert row["values"][double_schema["id"]]["result"] == pytest.approx(40.0)


def test_formula_with_syntax_error_stored_as_error_result(http_client):
    """Creating a schema with empty expression stores an error result on upsert."""
    db_id = _create_database(http_client)
    # No expression set → will produce "No expression configured" error
    schema = _create_schema(
        http_client, db_id, name="Empty", type_="formula", config={"expression": ""},
    )
    entry = _create_entry(http_client, db_id)
    # Trigger re-compute via any upsert
    text = _create_schema(http_client, db_id, name="Note", type_="text")
    _upsert_value(http_client, db_id, entry["id"], text["id"], {"text": "x"})

    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    row = next(e for e in entries if e["id"] == entry["id"])
    val = row["values"].get(schema["id"])
    assert val is not None
    assert val.get("error") is not None


# ─── Formula cycle detection ──────────────────────────────────────────────────


def test_create_formula_cycle_returns_422(http_client):
    """Creating a formula that references itself (self-referential) via another
    formula that references it back must be rejected."""
    db_id = _create_database(http_client)
    # Create A = prop('B') + 1  (B doesn't exist yet → no cycle initially)
    _create_formula_schema(http_client, db_id, "A", "prop('B') + 1")
    # Create B = prop('A') + 1  → now A depends on B and B depends on A: cycle!
    resp = http_client.post(
        f"/api/databases/{db_id}/schemas",
        json={"name": "B", "type": "formula", "config": {"expression": "prop('A') + 1"}},
    )
    assert resp.status_code == 422
    assert "circular" in resp.json()["detail"].lower()


def test_update_formula_cycle_returns_422(http_client):
    db_id = _create_database(http_client)
    a = _create_number_schema(http_client, db_id, "A")
    b = _create_formula_schema(http_client, db_id, "B", "prop('A') * 2")
    # Try to make A a formula that depends on B → cycle
    resp = http_client.patch(
        f"/api/databases/{db_id}/schemas/{a['id']}",
        json={"type": "formula", "config": {"expression": "prop('B') + 1"}},
    )
    assert resp.status_code == 422


# ─── Formula: validate endpoint ──────────────────────────────────────────────


def test_validate_formula_valid_expression(http_client):
    db_id = _create_database(http_client)
    resp = http_client.post(
        f"/api/databases/{db_id}/formulas/validate",
        json={"expression": "prop('Price') * prop('Qty') + 10"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["error"] is None
    assert set(data["prop_names"]) == {"Price", "Qty"}


def test_validate_formula_syntax_error(http_client):
    db_id = _create_database(http_client)
    resp = http_client.post(
        f"/api/databases/{db_id}/formulas/validate",
        json={"expression": "prop('X') +"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is False
    assert data["error"] is not None


def test_validate_formula_unknown_database_returns_404(http_client):
    resp = http_client.post(
        f"/api/databases/{uuid.uuid4()}/formulas/validate",
        json={"expression": "1 + 1"},
    )
    assert resp.status_code == 404


def test_validate_formula_no_props(http_client):
    db_id = _create_database(http_client)
    resp = http_client.post(
        f"/api/databases/{db_id}/formulas/validate",
        json={"expression": "round(3.14, 1)"},
    )
    assert resp.status_code == 200
    assert resp.json()["prop_names"] == []


# ─── Rollup property ──────────────────────────────────────────────────────────


def _create_rollup_schema(
    http_client,
    database_id: str,
    name: str,
    relation_schema_id: str,
    rollup_schema_id: str,
    function: str = "count",
) -> dict:
    return _create_schema(
        http_client, database_id, name=name, type_="rollup",
        config={
            "relation_schema_id": relation_schema_id,
            "rollup_schema_id": rollup_schema_id,
            "function": function,
        },
    )


def test_create_rollup_schema_returns_201(http_client):
    db_id = _create_database(http_client)
    rel = _create_relation_schema(http_client, db_id, db_id)
    num = _create_number_schema(http_client, db_id, "Score")
    schema = _create_rollup_schema(http_client, db_id, "Total Score", rel["id"], num["id"], "sum")
    assert schema["type"] == "rollup"
    assert schema["config"]["function"] == "sum"


def test_upsert_rollup_value_directly_returns_422(http_client):
    db_id = _create_database(http_client)
    rel = _create_relation_schema(http_client, db_id, db_id)
    num = _create_number_schema(http_client, db_id, "Score")
    schema = _create_rollup_schema(http_client, db_id, "Count", rel["id"], num["id"])
    entry = _create_entry(http_client, db_id)
    resp = http_client.put(
        f"/api/databases/{db_id}/entries/{entry['id']}/values/{schema['id']}",
        json={"value": {"result": 5}},
    )
    assert resp.status_code == 422


def test_rollup_count_computed_on_upsert(http_client):
    """Rollup count over relation reflects number of linked entries."""
    db_a = _create_database(http_client)
    db_b = _create_database(http_client)

    rel = _create_relation_schema(http_client, db_a, db_b, name="Links", direction="unilateral")
    score_col = _create_number_schema(http_client, db_b, "Score")
    count_schema = _create_rollup_schema(
        http_client, db_a, "Link Count", rel["id"], score_col["id"], "count"
    )

    entry_a = _create_entry(http_client, db_a)
    entry_b1 = _create_entry(http_client, db_b)
    entry_b2 = _create_entry(http_client, db_b)

    _upsert_value(
        http_client, db_a, entry_a["id"], rel["id"],
        {"related_ids": [entry_b1["id"], entry_b2["id"]]},
    )

    entries = http_client.get(f"/api/databases/{db_a}/entries").json()
    row = next(e for e in entries if e["id"] == entry_a["id"])
    val = row["values"].get(count_schema["id"])
    assert val is not None
    assert val["result"] == 2
    assert val["function"] == "count"


def test_rollup_sum_computed_correctly(http_client):
    db_a = _create_database(http_client)
    db_b = _create_database(http_client)

    rel = _create_relation_schema(http_client, db_a, db_b, name="Items")
    price_col = _create_number_schema(http_client, db_b, "Price")
    sum_schema = _create_rollup_schema(
        http_client, db_a, "Total Price", rel["id"], price_col["id"], "sum"
    )

    entry_a = _create_entry(http_client, db_a)
    entry_b1 = _create_entry(http_client, db_b)
    entry_b2 = _create_entry(http_client, db_b)

    _upsert_value(http_client, db_b, entry_b1["id"], price_col["id"], {"number": 10})
    _upsert_value(http_client, db_b, entry_b2["id"], price_col["id"], {"number": 25})
    _upsert_value(
        http_client, db_a, entry_a["id"], rel["id"],
        {"related_ids": [entry_b1["id"], entry_b2["id"]]},
    )

    entries = http_client.get(f"/api/databases/{db_a}/entries").json()
    row = next(e for e in entries if e["id"] == entry_a["id"])
    val = row["values"].get(sum_schema["id"])
    assert val["result"] == pytest.approx(35.0)


def test_rollup_invalid_function_returns_422(http_client):
    db_id = _create_database(http_client)
    rel = _create_relation_schema(http_client, db_id, db_id)
    num = _create_number_schema(http_client, db_id, "X")
    resp = http_client.post(
        f"/api/databases/{db_id}/schemas",
        json={
            "name": "Bad Rollup",
            "type": "rollup",
            "config": {
                "relation_schema_id": rel["id"],
                "rollup_schema_id": num["id"],
                "function": "product",
            },
        },
    )
    assert resp.status_code == 422


def test_same_db_rollup_updates_when_linked_entry_value_changes(http_client):
    """
    When a linked entry's value changes inside the same database, rollup schemas
    on the linking entry must be recomputed automatically.

    Regression test for the missing same-DB cascade:
    compute_cross_db_dependents skips the source database, so without
    compute_same_db_rollup_dependents the rollup would stay stale.
    """
    db_id = _create_database(http_client)

    rel = _create_relation_schema(http_client, db_id, db_id, name="Children")
    amount_col = _create_number_schema(http_client, db_id, "Amount")
    rollup = _create_rollup_schema(
        http_client, db_id, "Total Amount", rel["id"], amount_col["id"], "sum"
    )

    parent = _create_entry(http_client, db_id)
    child = _create_entry(http_client, db_id)

    _upsert_value(http_client, db_id, parent["id"], rel["id"], {"related_ids": [child["id"]]})
    _upsert_value(http_client, db_id, child["id"], amount_col["id"], {"number": 10})

    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    row = next(e for e in entries if e["id"] == parent["id"])
    assert row["values"][rollup["id"]]["result"] == pytest.approx(10.0)

    # Changing the child's value must propagate to the parent's rollup.
    _upsert_value(http_client, db_id, child["id"], amount_col["id"], {"number": 42})

    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    row = next(e for e in entries if e["id"] == parent["id"])
    assert row["values"][rollup["id"]]["result"] == pytest.approx(42.0)


def test_same_db_rollup_on_formula_column_updates_when_linked_entry_changes(http_client):
    """
    When a linked entry's *formula* result changes, a same-DB rollup that
    aggregates that formula column must also be recomputed.
    """
    db_id = _create_database(http_client)

    rel = _create_relation_schema(http_client, db_id, db_id, name="Children")
    base_col = _create_number_schema(http_client, db_id, "Base")
    formula = _create_formula_schema(http_client, db_id, "Doubled", "prop('Base') * 2")
    rollup = _create_rollup_schema(
        http_client, db_id, "Sum Doubled", rel["id"], formula["id"], "sum"
    )

    parent = _create_entry(http_client, db_id)
    child = _create_entry(http_client, db_id)

    _upsert_value(http_client, db_id, parent["id"], rel["id"], {"related_ids": [child["id"]]})
    _upsert_value(http_client, db_id, child["id"], base_col["id"], {"number": 5})

    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    row = next(e for e in entries if e["id"] == parent["id"])
    assert row["values"][rollup["id"]]["result"] == pytest.approx(10.0)

    # Changing base on the child changes its formula → parent rollup must follow.
    _upsert_value(http_client, db_id, child["id"], base_col["id"], {"number": 20})

    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    row = next(e for e in entries if e["id"] == parent["id"])
    assert row["values"][rollup["id"]]["result"] == pytest.approx(40.0)


def test_update_formula_config_recomputes_all_entries(http_client):
    """Updating a formula's expression should re-evaluate all existing entries."""
    db_id = _create_database(http_client)
    num = _create_number_schema(http_client, db_id, "Val")
    formula = _create_formula_schema(http_client, db_id, "Computed", "prop('Val') + 1")

    entry = _create_entry(http_client, db_id)
    _upsert_value(http_client, db_id, entry["id"], num["id"], {"number": 5})

    # Check initial result
    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    row = next(e for e in entries if e["id"] == entry["id"])
    assert row["values"][formula["id"]]["result"] == pytest.approx(6.0)

    # Update the expression
    http_client.patch(
        f"/api/databases/{db_id}/schemas/{formula['id']}",
        json={"config": {"expression": "prop('Val') * 10"}},
    )

    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    row = next(e for e in entries if e["id"] == entry["id"])
    assert row["values"][formula["id"]]["result"] == pytest.approx(50.0)


# ─── POST /api/databases/{id}/entries/query ───────────────────────────────────


def _query(http_client, database_id, *, filters=None, sorts=None, limit=1000, offset=0):
    """Helper: POST to /entries/query and assert 200."""
    body = {
        "filters": filters or [],
        "sorts":   sorts   or [],
        "limit":   limit,
        "offset":  offset,
    }
    resp = http_client.post(f"/api/databases/{database_id}/entries/query", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_query_entries_returns_200(http_client):
    db_id = _create_database(http_client)
    result = _query(http_client, db_id)
    assert "entries" in result
    assert "total" in result


def test_query_entries_unknown_database_returns_404(http_client):
    import uuid as _uuid
    resp = http_client.post(
        f"/api/databases/{_uuid.uuid4()}/entries/query",
        json={"filters": [], "sorts": []},
    )
    assert resp.status_code == 404


def test_query_entries_empty_database_returns_empty_list(http_client):
    db_id = _create_database(http_client)
    result = _query(http_client, db_id)
    assert result["entries"] == []
    assert result["total"] == 0


def test_query_entries_no_filters_returns_all(http_client):
    db_id = _create_database(http_client)
    _create_entry(http_client, db_id)
    _create_entry(http_client, db_id)
    result = _query(http_client, db_id)
    assert result["total"] == 2
    assert len(result["entries"]) == 2


def test_query_entries_name_contains_filter(http_client):
    db_id = _create_database(http_client)
    e1 = _create_entry(http_client, db_id)
    e2 = _create_entry(http_client, db_id)
    # Set titles via block update
    http_client.patch(f"/api/blocks/{e1['id']}", json={"content": {"title": "Napoleon"}})
    http_client.patch(f"/api/blocks/{e2['id']}", json={"content": {"title": "Wellington"}})

    result = _query(http_client, db_id, filters=[
        {"schema_id": "__name__", "operator": "contains", "value": "leon"}
    ])
    assert result["total"] == 1
    assert result["entries"][0]["id"] == e1["id"]


def test_query_entries_name_eq_filter_case_insensitive(http_client):
    db_id = _create_database(http_client)
    e1 = _create_entry(http_client, db_id)
    http_client.patch(f"/api/blocks/{e1['id']}", json={"content": {"title": "Napoleon"}})

    result = _query(http_client, db_id, filters=[
        {"schema_id": "__name__", "operator": "eq", "value": "napoleon"}
    ])
    assert result["total"] == 1


def test_query_entries_text_schema_filter(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id, name="Unit", type_="text")
    e1 = _create_entry(http_client, db_id)
    e2 = _create_entry(http_client, db_id)
    _upsert_value(http_client, db_id, e1["id"], schema["id"], {"text": "cavalry"})
    _upsert_value(http_client, db_id, e2["id"], schema["id"], {"text": "infantry"})

    result = _query(http_client, db_id, filters=[
        {"schema_id": schema["id"], "operator": "contains", "value": "cavalry"}
    ])
    assert result["total"] == 1
    assert result["entries"][0]["id"] == e1["id"]


def test_query_entries_number_gt_filter(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id, name="Strength", type_="number")
    e1 = _create_entry(http_client, db_id)
    e2 = _create_entry(http_client, db_id)
    _upsert_value(http_client, db_id, e1["id"], schema["id"], {"number": 100})
    _upsert_value(http_client, db_id, e2["id"], schema["id"], {"number": 5})

    result = _query(http_client, db_id, filters=[
        {"schema_id": schema["id"], "operator": "gt", "value": "50"}
    ])
    assert result["total"] == 1
    assert result["entries"][0]["id"] == e1["id"]


def test_query_entries_is_empty_filter(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id, name="Notes", type_="text")
    e1 = _create_entry(http_client, db_id)
    e2 = _create_entry(http_client, db_id)
    _upsert_value(http_client, db_id, e1["id"], schema["id"], {"text": "something"})
    # e2 has no value

    result = _query(http_client, db_id, filters=[
        {"schema_id": schema["id"], "operator": "is_empty", "value": ""}
    ])
    assert result["total"] == 1
    assert result["entries"][0]["id"] == e2["id"]


def test_query_entries_is_not_empty_filter(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id, name="Notes", type_="text")
    e1 = _create_entry(http_client, db_id)
    e2 = _create_entry(http_client, db_id)
    _upsert_value(http_client, db_id, e1["id"], schema["id"], {"text": "something"})

    result = _query(http_client, db_id, filters=[
        {"schema_id": schema["id"], "operator": "is_not_empty", "value": ""}
    ])
    assert result["total"] == 1
    assert result["entries"][0]["id"] == e1["id"]


def test_query_entries_multiple_filters_anded(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id, name="Rank", type_="number")
    e1 = _create_entry(http_client, db_id)
    e2 = _create_entry(http_client, db_id)
    http_client.patch(f"/api/blocks/{e1['id']}", json={"content": {"title": "Alpha"}})
    http_client.patch(f"/api/blocks/{e2['id']}", json={"content": {"title": "Beta"}})
    _upsert_value(http_client, db_id, e1["id"], schema["id"], {"number": 10})
    _upsert_value(http_client, db_id, e2["id"], schema["id"], {"number": 10})

    result = _query(http_client, db_id, filters=[
        {"schema_id": "__name__", "operator": "contains", "value": "Alpha"},
        {"schema_id": schema["id"], "operator": "gte", "value": "10"},
    ])
    assert result["total"] == 1
    assert result["entries"][0]["id"] == e1["id"]


def test_query_entries_sort_by_name_asc(http_client):
    db_id = _create_database(http_client)
    e1 = _create_entry(http_client, db_id)
    e2 = _create_entry(http_client, db_id)
    http_client.patch(f"/api/blocks/{e1['id']}", json={"content": {"title": "Zeta"}})
    http_client.patch(f"/api/blocks/{e2['id']}", json={"content": {"title": "Alpha"}})

    result = _query(http_client, db_id, sorts=[
        {"schema_id": "__name__", "direction": "asc"}
    ])
    titles = [e["content"]["title"] for e in result["entries"]]
    assert titles == sorted(titles, key=str.lower)


def test_query_entries_sort_by_number_schema_desc(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id, name="Rank", type_="number")
    e1 = _create_entry(http_client, db_id)
    e2 = _create_entry(http_client, db_id)
    e3 = _create_entry(http_client, db_id)
    _upsert_value(http_client, db_id, e1["id"], schema["id"], {"number": 1})
    _upsert_value(http_client, db_id, e2["id"], schema["id"], {"number": 3})
    _upsert_value(http_client, db_id, e3["id"], schema["id"], {"number": 2})

    result = _query(http_client, db_id, sorts=[
        {"schema_id": schema["id"], "direction": "desc"}
    ])
    ids = [e["id"] for e in result["entries"]]
    assert ids[0] == e2["id"]
    assert ids[-1] == e1["id"]


def test_query_entries_pagination_limit(http_client):
    db_id = _create_database(http_client)
    for _ in range(5):
        _create_entry(http_client, db_id)

    result = _query(http_client, db_id, limit=2)
    assert result["total"] == 5
    assert len(result["entries"]) == 2


def test_query_entries_pagination_offset(http_client):
    db_id = _create_database(http_client)
    for _ in range(5):
        _create_entry(http_client, db_id)

    result = _query(http_client, db_id, limit=2, offset=4)
    assert result["total"] == 5
    assert len(result["entries"]) == 1


def test_query_entries_limit_capped_at_10000(http_client):
    db_id = _create_database(http_client)
    result = _query(http_client, db_id, limit=99999)
    assert result["total"] == 0


def test_query_entries_unknown_schema_id_in_filter_is_skipped(http_client):
    import uuid as _uuid
    db_id = _create_database(http_client)
    _create_entry(http_client, db_id)
    result = _query(http_client, db_id, filters=[
        {"schema_id": str(_uuid.uuid4()), "operator": "eq", "value": "x"}
    ])
    assert result["total"] == 1  # unknown schema_id is skipped, no filter applied


def test_query_entries_existing_get_endpoint_still_works(http_client):
    """GET /entries must remain functional alongside the new query endpoint."""
    db_id = _create_database(http_client)
    _create_entry(http_client, db_id)
    resp = http_client.get(f"/api/databases/{db_id}/entries")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


# ─── Schema group field (#21) ─────────────────────────────────────────────────


def test_create_schema_default_group_is_standard(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id)
    assert schema["group"] == "Standard"


def test_create_schema_with_custom_group(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id, name="Phone", group="Kontakt")
    assert schema["group"] == "Kontakt"


def test_create_schema_group_returned_in_list(http_client):
    db_id = _create_database(http_client)
    _create_schema(http_client, db_id, name="A", group="Alpha")
    schemas = http_client.get(f"/api/databases/{db_id}/schemas").json()
    assert schemas[0]["group"] == "Alpha"


def test_update_schema_group(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id)
    updated = http_client.patch(
        f"/api/databases/{db_id}/schemas/{schema['id']}",
        json={"group": "Finanzen"},
    ).json()
    assert updated["group"] == "Finanzen"


def test_update_schema_group_does_not_reset_name(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id, name="Budget")
    updated = http_client.patch(
        f"/api/databases/{db_id}/schemas/{schema['id']}",
        json={"group": "Finanzen"},
    ).json()
    assert updated["name"] == "Budget"
    assert updated["group"] == "Finanzen"


def test_update_schema_without_group_keeps_existing_group(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id, group="Kontakt")
    updated = http_client.patch(
        f"/api/databases/{db_id}/schemas/{schema['id']}",
        json={"name": "NewName"},
    ).json()
    assert updated["group"] == "Kontakt"


# ── Timeline: _validate_timeline_value ───────────────────────────────────────

from app.blocks.database_router import (
    _validate_timeline_value,
    _pool_to_timeline,
    _parse_pool_range,
)


def test_validate_timeline_empty_is_valid():
    assert _validate_timeline_value({"_timeline": {}}) is None


def test_validate_timeline_singleton_empty_key():
    assert _validate_timeline_value({"_timeline": {"": {"text": "x"}}}) is None


def test_validate_timeline_empty_key_with_others_invalid():
    val = {"_timeline": {"": {"text": "x"}, "2024-01-01T00:00:00→": {"text": "y"}}}
    assert _validate_timeline_value(val) is not None


def test_validate_timeline_valid_full_range():
    val = {"_timeline": {
        "2023-01-01T00:00:00→2023-12-31T23:59:59": {"text": "a"},
        "2024-01-01T00:00:00→": {"text": "b"},
    }}
    assert _validate_timeline_value(val) is None


def test_validate_timeline_valid_until_range_first():
    val = {"_timeline": {
        "→2022-12-31T23:59:59": {"text": "before"},
        "2023-01-01T00:00:00→": {"text": "after"},
    }}
    assert _validate_timeline_value(val) is None


def test_validate_timeline_until_not_first_is_invalid():
    # The function sorts keys chronologically before validating, so →until
    # always ends up at position 0 (valid). No overlap exists between
    # →2022-06-30 and 2023-01-01→ so the function returns None (valid).
    val = {"_timeline": {
        "2023-01-01T00:00:00→2023-12-31T23:59:59": {"text": "a"},
        "→2022-06-30T23:59:59": {"text": "before"},
    }}
    assert _validate_timeline_value(val) is None


def test_validate_timeline_since_not_last_is_invalid():
    val = {"_timeline": {
        "2023-01-01T00:00:00→": {"text": "open"},
        "2024-01-01T00:00:00→2024-12-31T23:59:59": {"text": "closed"},
    }}
    assert _validate_timeline_value(val) is not None


def test_validate_timeline_overlap_is_invalid():
    val = {"_timeline": {
        "2023-01-01T00:00:00→2024-06-30T23:59:59": {"text": "a"},
        "2024-01-01T00:00:00→": {"text": "b"},
    }}
    assert _validate_timeline_value(val) is not None


def test_validate_timeline_not_a_dict_is_invalid():
    assert _validate_timeline_value({"_timeline": "oops"}) is not None


# ── Timeline: _pool_to_timeline sweepline ────────────────────────────────────

def test_pool_to_timeline_empty_pool():
    assert _pool_to_timeline({}) == {}


def test_pool_to_timeline_single_since():
    pool = {"uuid-c": ["2023-01-01T00:00:00→"]}
    timeline = _pool_to_timeline(pool)
    assert "2023-01-01T00:00:00→" in timeline
    assert timeline["2023-01-01T00:00:00→"]["related_ids"] == ["uuid-c"]


def test_pool_to_timeline_full_example():
    pool = {
        "uuid-a": ["2024-01-01T00:00:00→2024-12-31T23:59:59", "2026-01-01T00:00:00→"],
        "uuid-c": ["2023-01-01T00:00:00→"],
    }
    timeline = _pool_to_timeline(pool)
    # uuid-c alone in 2023
    assert any(
        "uuid-c" in v.get("related_ids", []) and "uuid-a" not in v.get("related_ids", [])
        for k, v in timeline.items()
        if k.startswith("2023")
    )
    # Both active in 2024
    assert any(
        "uuid-a" in v.get("related_ids", []) and "uuid-c" in v.get("related_ids", [])
        for k, v in timeline.items()
        if k.startswith("2024")
    )
    # Open-ended slot has both
    open_slots = [k for k in timeline if k.endswith("→")]
    assert any(
        "uuid-a" in timeline[k].get("related_ids", [])
        for k in open_slots
        if k.startswith("2026")
    )


# ── Timeline: upsert endpoint validation ─────────────────────────────────────

def test_upsert_timeline_value_valid(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id, type_="text",
                            config={"hasTimeline": True})
    entry = _create_entry(http_client, db_id)
    resp = http_client.put(
        f"/api/databases/{db_id}/entries/{entry['id']}/values/{schema['id']}",
        json={"value": {"_timeline": {
            "2023-01-01T00:00:00→2023-12-31T23:59:59": {"text": "Schüler"},
            "2024-01-01T00:00:00→": {"text": "Meister"},
        }}},
    )
    assert resp.status_code == 204


def test_upsert_timeline_value_overlapping_rejected(http_client):
    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id, type_="text",
                            config={"hasTimeline": True})
    entry = _create_entry(http_client, db_id)
    resp = http_client.put(
        f"/api/databases/{db_id}/entries/{entry['id']}/values/{schema['id']}",
        json={"value": {"_timeline": {
            "2023-01-01T00:00:00→2024-06-30T23:59:59": {"text": "a"},
            "2024-01-01T00:00:00→": {"text": "b"},
        }}},
    )
    assert resp.status_code == 422


def test_upsert_relation_timeline_pool_write(http_client):
    db_id = _create_database(http_client)
    schema = _create_relation_schema(
        http_client, db_id, db_id,
        name="Links", direction="unilateral",
    )
    http_client.patch(
        f"/api/databases/{db_id}/schemas/{schema['id']}",
        json={"config": {
            **schema["config"],
            "hasTimeline": True,
        }},
    )
    entry_a = _create_entry(http_client, db_id)
    entry_b = _create_entry(http_client, db_id)
    pool = {entry_b["id"]: ["2024-01-01T00:00:00→"]}
    resp = http_client.put(
        f"/api/databases/{db_id}/entries/{entry_a['id']}/values/{schema['id']}",
        json={"value": {"relationPool": pool}},
    )
    assert resp.status_code == 204
    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    row_a = next(e for e in entries if e["id"] == entry_a["id"])
    val = row_a["values"].get(schema["id"])
    assert val is not None
    assert "_timeline" in val
    assert "relationPool" in val


def test_upsert_relation_timeline_direct_timeline_write_rejected(http_client):
    db_id = _create_database(http_client)
    schema = _create_relation_schema(
        http_client, db_id, db_id,
        name="Links", direction="unilateral",
    )
    http_client.patch(
        f"/api/databases/{db_id}/schemas/{schema['id']}",
        json={"config": {**schema["config"], "hasTimeline": True}},
    )
    entry_a = _create_entry(http_client, db_id)
    entry_b = _create_entry(http_client, db_id)
    resp = http_client.put(
        f"/api/databases/{db_id}/entries/{entry_a['id']}/values/{schema['id']}",
        json={"value": {"_timeline": {
            "2024-01-01T00:00:00→": {"related_ids": [entry_b["id"]]},
        }}},
    )
    assert resp.status_code == 422


# ── hasTimeline propagation to bilateral mirror ───────────────────────────────

def test_update_schema_has_timeline_propagates_to_bilateral_mirror(http_client):
    """Enabling hasTimeline on a bilateral relation also sets it on the mirror."""
    db_a = _create_database(http_client)
    db_b = _create_database(http_client)

    # Create a bilateral relation from db_a → db_b
    schema = _create_relation_schema(
        http_client, db_a, db_b,
        name="Linked",
        direction="bilateral",
        mirror_name="Linked Back",
    )

    # Enable hasTimeline on the source schema
    http_client.patch(
        f"/api/databases/{db_a}/schemas/{schema['id']}",
        json={"config": {
            **schema["config"],
            "hasTimeline": True,
        }},
    )

    # Mirror schema in db_b should now also have hasTimeline=True
    schemas_b = http_client.get(f"/api/databases/{db_b}/schemas").json()
    mirror = next((s for s in schemas_b if s["name"] == "Linked Back"), None)
    assert mirror is not None, "Mirror schema not found"
    assert mirror["config"].get("hasTimeline") is True


def test_update_schema_has_timeline_disabled_propagates_to_mirror(http_client):
    """Disabling hasTimeline also propagates to the mirror."""
    db_a = _create_database(http_client)
    db_b = _create_database(http_client)

    schema = _create_relation_schema(
        http_client, db_a, db_b,
        name="Linked",
        direction="bilateral",
        mirror_name="Linked Back",
    )

    # Enable first
    http_client.patch(
        f"/api/databases/{db_a}/schemas/{schema['id']}",
        json={"config": {**schema["config"], "hasTimeline": True}},
    )
    # Then disable
    http_client.patch(
        f"/api/databases/{db_a}/schemas/{schema['id']}",
        json={"config": {**schema["config"], "hasTimeline": False}},
    )

    schemas_b = http_client.get(f"/api/databases/{db_b}/schemas").json()
    mirror = next((s for s in schemas_b if s["name"] == "Linked Back"), None)
    assert mirror is not None
    assert not mirror["config"].get("hasTimeline", False)


def test_update_schema_has_timeline_no_propagation_for_unilateral(http_client):
    """hasTimeline on a unilateral relation does NOT affect any mirror."""
    db_a = _create_database(http_client)
    db_b = _create_database(http_client)

    schema = _create_relation_schema(
        http_client, db_a, db_b,
        name="One-way",
        direction="unilateral",
    )

    resp = http_client.patch(
        f"/api/databases/{db_a}/schemas/{schema['id']}",
        json={"config": {**schema["config"], "hasTimeline": True}},
    )
    # Should succeed without error — no mirror to propagate to
    assert resp.status_code == 200
    assert resp.json()["config"].get("hasTimeline") is True


# ── Relation timeline value migration (enable hasTimeline on existing data) ───

def test_enable_timeline_migrates_existing_relation_values_to_always_valid(http_client):
    """
    Enabling hasTimeline on a relation schema with existing flat values
    converts those values to pool/timeline format with always-valid ("") ranges.
    """
    db_id = _create_database(http_client)
    schema = _create_relation_schema(
        http_client, db_id, db_id,
        name="Links", direction="unilateral",
    )
    entry_a = _create_entry(http_client, db_id)
    entry_b = _create_entry(http_client, db_id)

    # Write a flat relation value before enabling timeline
    _upsert_value(
        http_client, db_id, entry_a["id"], schema["id"],
        {"related_ids": [entry_b["id"]]},
    )

    # Enable hasTimeline
    resp = http_client.patch(
        f"/api/databases/{db_id}/schemas/{schema['id']}",
        json={"config": {**schema["config"], "hasTimeline": True}},
    )
    assert resp.status_code == 200

    # The existing value must now be in pool/timeline format
    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    row_a = next(e for e in entries if e["id"] == entry_a["id"])
    val = row_a["values"].get(schema["id"])
    assert val is not None, "Value must not be None after migration"
    assert "relationPool" in val, "Value must have relationPool after migration"
    assert "_timeline" in val, "Value must have _timeline after migration"
    pool = val["relationPool"]
    assert entry_b["id"] in pool, "Linked entry must appear in pool"
    assert pool[entry_b["id"]] == [""], "Linked entry must have always-valid range"
    assert "" in val["_timeline"], "Timeline must contain the always-valid slot"
    assert entry_b["id"] in val["_timeline"][""]["related_ids"]


def test_enable_timeline_leaves_empty_relation_values_untouched(http_client):
    """Entries with no relation value (or empty related_ids) are not written."""
    db_id = _create_database(http_client)
    schema = _create_relation_schema(
        http_client, db_id, db_id,
        name="Links", direction="unilateral",
    )
    entry_a = _create_entry(http_client, db_id)
    # entry_a intentionally has no value for schema

    resp = http_client.patch(
        f"/api/databases/{db_id}/schemas/{schema['id']}",
        json={"config": {**schema["config"], "hasTimeline": True}},
    )
    assert resp.status_code == 200

    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    row_a = next(e for e in entries if e["id"] == entry_a["id"])
    val = row_a["values"].get(schema["id"])
    # No value should have been created for an entry that had none
    assert val is None or val == {}


def test_enable_timeline_skips_values_already_in_pool_format(http_client):
    """Values already in pool format are not double-migrated."""
    db_id = _create_database(http_client)
    schema = _create_relation_schema(
        http_client, db_id, db_id,
        name="Links", direction="unilateral",
    )
    entry_a = _create_entry(http_client, db_id)
    entry_b = _create_entry(http_client, db_id)

    # Enable first time
    http_client.patch(
        f"/api/databases/{db_id}/schemas/{schema['id']}",
        json={"config": {**schema["config"], "hasTimeline": True}},
    )
    # Write a bounded-range pool entry
    bounded_pool = {entry_b["id"]: ["2024-01-01T00:00:00→"]}
    _upsert_value(
        http_client, db_id, entry_a["id"], schema["id"],
        {"relationPool": bounded_pool},
    )

    # Disable and re-enable (simulates a double-toggle edge case)
    http_client.patch(
        f"/api/databases/{db_id}/schemas/{schema['id']}",
        json={"config": {**schema["config"], "hasTimeline": False}},
    )
    http_client.patch(
        f"/api/databases/{db_id}/schemas/{schema['id']}",
        json={"config": {**schema["config"], "hasTimeline": True}},
    )

    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    row_a = next(e for e in entries if e["id"] == entry_a["id"])
    val = row_a["values"].get(schema["id"])
    # The bounded range should still be there — not overwritten with ""
    assert val is not None
    assert "relationPool" in val


def test_enable_timeline_on_bilateral_relation_migrates_both_sides(http_client):
    """
    Enabling hasTimeline on a bilateral relation migrates existing flat values
    on both the source and the mirror side.
    """
    db_a = _create_database(http_client)
    db_b = _create_database(http_client)

    schema = _create_relation_schema(
        http_client, db_a, db_b,
        name="Linked",
        direction="bilateral",
        mirror_name="Linked Back",
    )

    entry_a = _create_entry(http_client, db_a)
    entry_b = _create_entry(http_client, db_b)

    # Write a flat relation value (bilateral sync writes the mirror automatically)
    _upsert_value(
        http_client, db_a, entry_a["id"], schema["id"],
        {"related_ids": [entry_b["id"]]},
    )

    # Enable hasTimeline on the source schema
    resp = http_client.patch(
        f"/api/databases/{db_a}/schemas/{schema['id']}",
        json={"config": {**schema["config"], "hasTimeline": True}},
    )
    assert resp.status_code == 200

    # Source side: entry_a's value must be in pool format
    entries_a = http_client.get(f"/api/databases/{db_a}/entries").json()
    row_a = next(e for e in entries_a if e["id"] == entry_a["id"])
    val_a = row_a["values"].get(schema["id"])
    assert val_a is not None
    assert "relationPool" in val_a
    assert entry_b["id"] in val_a["relationPool"]
    assert val_a["relationPool"][entry_b["id"]] == [""]

    # Mirror side: entry_b's mirror value must also be in pool format
    schemas_b = http_client.get(f"/api/databases/{db_b}/schemas").json()
    mirror_schema = next(s for s in schemas_b if s["name"] == "Linked Back")
    entries_b = http_client.get(f"/api/databases/{db_b}/entries").json()
    row_b = next(e for e in entries_b if e["id"] == entry_b["id"])
    val_b = row_b["values"].get(mirror_schema["id"])
    assert val_b is not None
    assert "relationPool" in val_b
    assert entry_a["id"] in val_b["relationPool"]
    assert val_b["relationPool"][entry_a["id"]] == [""]


# ── _pool_to_timeline: always-valid and invalid-timestamp edge cases ──────────

def test_pool_to_timeline_always_valid_only():
    """A pool with only "" ranges emits a single "" slot."""
    pool = {"uuid-a": [""], "uuid-b": [""]}
    timeline = _pool_to_timeline(pool)
    assert list(timeline.keys()) == [""]
    assert set(timeline[""]["related_ids"]) == {"uuid-a", "uuid-b"}


def test_pool_to_timeline_always_valid_mixed_with_bounded():
    """Always-valid UIDs appear in every bounded slot."""
    pool = {
        "always": [""],
        "bounded": ["2024-01-01T00:00:00→2024-12-31T23:59:59"],
    }
    timeline = _pool_to_timeline(pool)
    # No "" key when bounded slots exist
    assert "" not in timeline
    # Every slot contains the always-valid uid
    for slot_val in timeline.values():
        assert "always" in slot_val["related_ids"]


def test_pool_to_timeline_invalid_timestamp_skipped():
    """An invalid timestamp string (e.g. '2024') is skipped without crashing."""
    pool = {"uuid-a": ["2024"]}
    # Should not raise; unparseable timestamps are silently dropped
    result = _pool_to_timeline(pool)
    assert isinstance(result, dict)


def test_pool_to_timeline_empty_range_round_trip(http_client):
    """Saving a pool with '' range stores it and the cell resolves correctly."""
    db_id = _create_database(http_client)
    schema = _create_relation_schema(
        http_client, db_id, db_id,
        name="Links", direction="unilateral",
    )
    http_client.patch(
        f"/api/databases/{db_id}/schemas/{schema['id']}",
        json={"config": {**schema["config"], "hasTimeline": True}},
    )
    entry_a = _create_entry(http_client, db_id)
    entry_b = _create_entry(http_client, db_id)

    # Write an always-valid pool entry
    resp = http_client.put(
        f"/api/databases/{db_id}/entries/{entry_a['id']}/values/{schema['id']}",
        json={"value": {"relationPool": {entry_b["id"]: [""]}}},
    )
    assert resp.status_code == 204

    # Fetch back and verify _timeline has the "" slot
    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    row = next(e for e in entries if e["id"] == entry_a["id"])
    val = row["values"].get(schema["id"])
    assert val is not None
    assert "_timeline" in val
    assert "" in val["_timeline"]
    assert entry_b["id"] in val["_timeline"][""]["related_ids"]


# ─── Automation engine integration ────────────────────────────────────────────


def test_upsert_pings_automation_engine(http_client, monkeypatch):
    """
    Verify that a successful property value upsert fires the automation
    engine with a correctly populated TriggerEvent.
    """
    import app.blocks.database_router as dr

    received: list = []

    async def fake_receive(event, db):
        received.append(event)

    monkeypatch.setattr(dr, "automation_receive", fake_receive)

    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id, name="Status", type_="select")
    entry = _create_entry(http_client, db_id)
    _upsert_value(
        http_client, db_id, entry["id"], schema["id"], {"option": "Done"}
    )

    assert len(received) == 1, "Engine must be called exactly once per upsert"
    ev = received[0]
    assert ev.action_type == "PropertyUpdate"
    assert ev.origin == "user"
    assert ev.db_uuid == db_id
    assert ev.property_uuid == schema["id"]
    assert ev.entry_id == entry["id"]


def test_upsert_engine_failure_does_not_break_response(http_client, monkeypatch):
    """
    If the automation engine raises an exception it must be swallowed and
    the HTTP response must still be 204.
    """
    import app.blocks.database_router as dr

    async def exploding_receive(event, db):
        raise RuntimeError("engine on fire")

    monkeypatch.setattr(dr, "automation_receive", exploding_receive)

    db_id = _create_database(http_client)
    schema = _create_schema(http_client, db_id, name="Status", type_="select")
    entry = _create_entry(http_client, db_id)
    resp = http_client.put(
        f"/api/databases/{db_id}/entries/{entry['id']}/values/{schema['id']}",
        json={"value": {"option": "Done"}},
    )
    assert resp.status_code == 204


# ─── Entry-template endpoints ─────────────────────────────────────────────────


def _create_entry_template(http_client, database_id: str) -> dict:
    resp = http_client.post(f"/api/databases/{database_id}/entry-templates")
    assert resp.status_code == 201
    return resp.json()


# ── POST /{database_id}/entry-templates ───────────────────────────────────────


def test_create_entry_template_returns_201(http_client):
    db_id = _create_database(http_client)
    resp = http_client.post(f"/api/databases/{db_id}/entry-templates")
    assert resp.status_code == 201


def test_create_entry_template_response_has_id(http_client):
    db_id = _create_database(http_client)
    tmpl = _create_entry_template(http_client, db_id)
    assert "id" in tmpl


def test_create_entry_template_unknown_database_returns_404(http_client):
    resp = http_client.post(f"/api/databases/{uuid.uuid4()}/entry-templates")
    assert resp.status_code == 404


# ── GET /{database_id}/entry-templates ────────────────────────────────────────


def test_list_entry_templates_returns_200(http_client):
    db_id = _create_database(http_client)
    resp = http_client.get(f"/api/databases/{db_id}/entry-templates")
    assert resp.status_code == 200


def test_list_entry_templates_empty_for_new_database(http_client):
    db_id = _create_database(http_client)
    result = http_client.get(f"/api/databases/{db_id}/entry-templates").json()
    assert result == []


def test_list_entry_templates_returns_created_template(http_client):
    db_id = _create_database(http_client)
    tmpl = _create_entry_template(http_client, db_id)
    result = http_client.get(f"/api/databases/{db_id}/entry-templates").json()
    assert any(t["id"] == tmpl["id"] for t in result)


def test_list_entry_templates_does_not_include_regular_entries(http_client):
    db_id = _create_database(http_client)
    _create_entry(http_client, db_id)
    _create_entry_template(http_client, db_id)
    result = http_client.get(f"/api/databases/{db_id}/entry-templates").json()
    assert len(result) == 1


def test_list_entry_templates_unknown_database_returns_404(http_client):
    resp = http_client.get(f"/api/databases/{uuid.uuid4()}/entry-templates")
    assert resp.status_code == 404


# ── Templates excluded from regular entry queries ─────────────────────────────


def test_entry_templates_excluded_from_list_entries(http_client):
    """GET /entries must never include entry_template blocks."""
    db_id = _create_database(http_client)
    real_entry = _create_entry(http_client, db_id)
    _create_entry_template(http_client, db_id)
    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    assert len(entries) == 1
    assert entries[0]["id"] == real_entry["id"]


def test_entry_templates_excluded_from_query_entries(http_client):
    """POST /entries/query must never include entry_template blocks."""
    db_id = _create_database(http_client)
    real_entry = _create_entry(http_client, db_id)
    _create_entry_template(http_client, db_id)
    resp = http_client.post(
        f"/api/databases/{db_id}/entries/query",
        json={"filter_groups": [], "sorts": [], "limit": 100, "offset": 0},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["entries"][0]["id"] == real_entry["id"]


# ── POST /{database_id}/entry-templates/{template_id}/apply/{entry_id} ────────


def test_apply_entry_template_returns_204(http_client):
    db_id = _create_database(http_client)
    tmpl = _create_entry_template(http_client, db_id)
    entry = _create_entry(http_client, db_id)
    resp = http_client.post(
        f"/api/databases/{db_id}/entry-templates/{tmpl['id']}/apply/{entry['id']}"
    )
    assert resp.status_code == 204


def test_apply_entry_template_copies_text_property(http_client):
    db_id = _create_database(http_client)
    text_schema = _create_schema(http_client, db_id, name="Notes", type_="text")
    tmpl = _create_entry_template(http_client, db_id)
    _upsert_value(http_client, db_id, tmpl["id"], text_schema["id"], {"text": "From template"})

    entry = _create_entry(http_client, db_id)
    http_client.post(
        f"/api/databases/{db_id}/entry-templates/{tmpl['id']}/apply/{entry['id']}"
    )

    entries = http_client.get(f"/api/databases/{db_id}/entries").json()
    row = next(e for e in entries if e["id"] == entry["id"])
    assert row["values"].get(text_schema["id"], {}).get("text") == "From template"


def test_apply_entry_template_skips_readonly_properties(http_client):
    """Readonly properties (id, created_*) must not be overwritten on apply."""
    db_id = _create_database(http_client)
    _seed(http_client, db_id)  # seeds id, created_by, created_time, …

    tmpl = _create_entry_template(http_client, db_id)
    entry = _create_entry(http_client, db_id)

    # Capture the entry's own id value before apply.
    schemas = http_client.get(f"/api/databases/{db_id}/schemas").json()
    id_schema = next(s for s in schemas if s["type"] == "id")
    entries_before = http_client.get(f"/api/databases/{db_id}/entries").json()
    row_before = next(e for e in entries_before if e["id"] == entry["id"])
    id_val_before = row_before["values"].get(id_schema["id"])

    http_client.post(
        f"/api/databases/{db_id}/entry-templates/{tmpl['id']}/apply/{entry['id']}"
    )

    entries_after = http_client.get(f"/api/databases/{db_id}/entries").json()
    row_after = next(e for e in entries_after if e["id"] == entry["id"])
    assert row_after["values"].get(id_schema["id"]) == id_val_before


def test_apply_entry_template_unknown_template_returns_404(http_client):
    db_id = _create_database(http_client)
    entry = _create_entry(http_client, db_id)
    resp = http_client.post(
        f"/api/databases/{db_id}/entry-templates/{uuid.uuid4()}/apply/{entry['id']}"
    )
    assert resp.status_code == 404


def test_apply_entry_template_unknown_entry_returns_404(http_client):
    db_id = _create_database(http_client)
    tmpl = _create_entry_template(http_client, db_id)
    resp = http_client.post(
        f"/api/databases/{db_id}/entry-templates/{tmpl['id']}/apply/{uuid.uuid4()}"
    )
    assert resp.status_code == 404


def test_apply_regular_entry_as_template_returns_404(http_client):
    """Passing a regular page entry as the template_id must return 404."""
    db_id = _create_database(http_client)
    not_a_template = _create_entry(http_client, db_id)
    target = _create_entry(http_client, db_id)
    resp = http_client.post(
        f"/api/databases/{db_id}/entry-templates/{not_a_template['id']}/apply/{target['id']}"
    )
    assert resp.status_code == 404
