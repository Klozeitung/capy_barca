"""
Object-level permission tests for the database router.

Every other test of the router runs as an admin, and admins pass every
permission check, so those tests cannot notice a handler that forgets to ask.
The tests here set the scene as the admin, then switch the client to a member
account (``login_as``) and repeat the requests against a database that member
may not access.

Expected answers
----------------
* Reads answer 404, the same as for a database that does not exist.
* Changes answer 403.
* The entry listings relation pickers call answer with an empty result.
* Writing a relation property also needs write access to the relation's
  target.
* A member with read access passes every read and is refused every change
  with 403 and the read-only detail.

``test_every_route_has_a_refusal_case`` enumerates the router's routes, so a
new endpoint fails the suite until a refusal case for it is added below.
"""
import uuid
from dataclasses import dataclass

import pytest

from app.blocks.database_router import database_router
from app.blocks.models import WORKSPACE_ROOT_ID
from app.session.deps import NO_ACCESS_DETAIL, READ_ONLY_DETAIL


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _create_database(client, title: str | None = None) -> str:
    """
    Create a database under the workspace root, open to everyone.

    First-level blocks start private to their creator; the tests open them
    explicitly and make the ones they need private afterwards.
    """
    body: dict = {"type": "database", "parent_id": str(WORKSPACE_ROOT_ID)}
    if title:
        body["content"] = {"title": title}
    resp = client.post("/api/blocks", json=body)
    assert resp.status_code == 201
    database_id = resp.json()["id"]
    _set_mode(client, database_id, "everyone")
    return database_id


def _set_mode(
    client,
    block_id: str,
    mode: str,
    grants: list[str] | None = None,
    *,
    level: str = "write",
) -> None:
    """Set *mode*; every account in *grants* and the level for everyone get *level*."""
    resp = client.put(
        f"/api/blocks/{block_id}/permissions",
        json={
            "mode": mode,
            "everyone_level": level,
            "grants": [{"user_id": user_id, "level": level} for user_id in grants or []],
        },
    )
    assert resp.status_code == 200, resp.text


def _create_schema(client, database_id: str, name: str, type_: str, config: dict | None = None) -> dict:
    body: dict = {"name": name, "type": type_}
    if config is not None:
        body["config"] = config
    resp = client.post(f"/api/databases/{database_id}/schemas", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _relation_config(target_id: str, direction: str = "bilateral", mirror: str = "Back A") -> dict:
    return {
        "target_database_id": target_id,
        "direction": direction,
        "mirror_property_name": mirror,
    }


def _create_entry(client, database_id: str) -> str:
    resp = client.post(f"/api/databases/{database_id}/entries")
    assert resp.status_code == 201
    return resp.json()["id"]


def _create_template(client, database_id: str) -> str:
    resp = client.post(f"/api/databases/{database_id}/entry-templates")
    assert resp.status_code == 201
    return resp.json()["id"]


def _put_value(client, database_id: str, entry_id: str, schema_id: str, value):
    return client.put(
        f"/api/databases/{database_id}/entries/{entry_id}/values/{schema_id}",
        json={"value": value},
    )


@dataclass
class Scene:
    """A private database with content in it, set up by the admin."""

    db: str
    schema: str
    entry: str
    template: str


@pytest.fixture
def private_scene(http_client) -> Scene:
    """
    A database only its owner (the default admin) may access, holding a text
    property, an entry with a value and an entry template.
    """
    db_id = _create_database(http_client, title="Private X")
    schema = _create_schema(http_client, db_id, "Field A", "text")
    entry = _create_entry(http_client, db_id)
    assert _put_value(http_client, db_id, entry, schema["id"], {"text": "Value A"}).status_code == 200
    template = _create_template(http_client, db_id)
    _set_mode(http_client, db_id, "private")
    return Scene(db=db_id, schema=schema["id"], entry=entry, template=template)


# ─── Refusal matrix ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Case:
    method: str
    route: str          # route path as registered on the router
    expected: int       # status a member without access receives
    body: dict | None = None

    def url(self, scene: Scene) -> str:
        return (
            self.route
            .replace("{database_id}", scene.db)
            .replace("{schema_id}", scene.schema)
            .replace("{entry_id}", scene.entry)
            .replace("{template_id}", scene.template)
            .replace("{option_id}", "option-x")
        )


_PREFIX = "/api/databases"

_REFUSALS: list[Case] = [
    Case("GET", f"{_PREFIX}/{{database_id}}/schemas", 404),
    Case("POST", f"{_PREFIX}/{{database_id}}/schemas", 403, {"name": "Field B", "type": "text"}),
    Case("PATCH", f"{_PREFIX}/{{database_id}}/schemas/{{schema_id}}", 403, {"name": "Field C"}),
    Case("DELETE", f"{_PREFIX}/{{database_id}}/schemas/{{schema_id}}", 403),
    Case("GET", f"{_PREFIX}/{{database_id}}/schemas/{{schema_id}}/options/{{option_id}}/usage", 404),
    Case("GET", f"{_PREFIX}/{{database_id}}/schemas/{{schema_id}}/key-references", 404),
    Case("POST", f"{_PREFIX}/{{database_id}}/formulas/validate", 404, {"expression": "1 + 1"}),
    Case("GET", f"{_PREFIX}/{{database_id}}/entries/{{entry_id}}", 404),
    Case("POST", f"{_PREFIX}/{{database_id}}/entries/batch", 404, {"ids": []}),
    Case("POST", f"{_PREFIX}/{{database_id}}/entries", 403),
    Case("POST", f"{_PREFIX}/{{database_id}}/entries/{{entry_id}}/duplicate", 403),
    Case("POST", f"{_PREFIX}/{{database_id}}/seed-readonly-schemas", 403),
    Case(
        "PUT", f"{_PREFIX}/{{database_id}}/entries/{{entry_id}}/values/{{schema_id}}", 403,
        {"value": {"text": "Value B"}},
    ),
    Case("POST", f"{_PREFIX}/{{database_id}}/entry-templates", 403),
    Case("GET", f"{_PREFIX}/{{database_id}}/entry-templates", 404),
    Case("POST", f"{_PREFIX}/{{database_id}}/entry-templates/{{template_id}}/apply/{{entry_id}}", 403),
]

# Listings that answer an inaccessible database with an empty result, so a
# relation picker pointed at it renders nothing instead of failing.
_EMPTY_RESULTS: list[tuple[Case, object]] = [
    (Case("GET", f"{_PREFIX}/{{database_id}}/entries", 200), []),
    (Case("POST", f"{_PREFIX}/{{database_id}}/entries/query", 200, {}), {"entries": [], "total": 0}),
    (Case("POST", f"{_PREFIX}/{{database_id}}/entries/resolve-titles", 200, {"ids": []}), []),
]

# GET /api/databases has no database id; its filtering is tested separately.
_UNSCOPED = {("GET", _PREFIX)}


def _send(client, case: Case, scene: Scene):
    kwargs = {"json": case.body} if case.body is not None else {}
    return client.request(case.method, case.url(scene), **kwargs)


def test_every_route_has_a_refusal_case():
    """A route added to the router without a case here fails this test."""
    registered = {
        (method, route.path)
        for route in database_router.routes
        for method in route.methods
    }
    covered = {(c.method, c.route) for c in _REFUSALS}
    covered |= {(c.method, c.route) for c, _ in _EMPTY_RESULTS}
    covered |= _UNSCOPED
    assert registered == covered


@pytest.mark.parametrize("case", _REFUSALS, ids=lambda c: f"{c.method} {c.route}")
def test_member_is_refused_on_a_private_database(
    http_client, login_as, member_user, private_scene, case,
):
    login_as(member_user)
    response = _send(http_client, case, private_scene)
    assert response.status_code == case.expected, response.text


@pytest.mark.parametrize(
    "case,expected_body", _EMPTY_RESULTS, ids=lambda c: getattr(c, "route", ""),
)
def test_member_gets_an_empty_listing_of_a_private_database(
    http_client, login_as, member_user, private_scene, case, expected_body,
):
    if case.route.endswith("resolve-titles"):
        case = Case(case.method, case.route, case.expected, {"ids": [private_scene.entry]})
    login_as(member_user)
    response = _send(http_client, case, private_scene)
    assert response.status_code == 200
    assert response.json() == expected_body


@pytest.mark.parametrize("case", _REFUSALS, ids=lambda c: f"{c.method} {c.route}")
def test_admin_passes_where_the_member_is_refused(http_client, private_scene, case):
    """Positive control: the same request succeeds for the owner."""
    response = _send(http_client, case, private_scene)
    assert response.status_code < 400, response.text


@pytest.mark.parametrize("case", _REFUSALS, ids=lambda c: f"{c.method} {c.route}")
def test_whitelisted_member_passes(http_client, login_as, member_user, private_scene, case):
    """A write grant opens every endpoint."""
    _set_mode(http_client, private_scene.db, "whitelist", [str(member_user.id)])
    login_as(member_user)
    response = _send(http_client, case, private_scene)
    assert response.status_code < 400, response.text


@pytest.mark.parametrize("case", _REFUSALS, ids=lambda c: f"{c.method} {c.route}")
def test_reader_passes_reads_and_is_refused_changes(
    http_client, login_as, member_user, private_scene, case,
):
    """
    A read grant opens every endpoint that answers a stranger with 404, and
    refuses every one that answers 403, now with the read-only detail.
    """
    _set_mode(http_client, private_scene.db, "whitelist", [str(member_user.id)], level="read")
    login_as(member_user)
    response = _send(http_client, case, private_scene)
    if case.expected == 404:
        assert response.status_code < 400, response.text
    else:
        assert response.status_code == 403
        assert response.json()["detail"] == READ_ONLY_DETAIL


def test_reader_of_an_everyone_database_is_refused_changes(
    http_client, login_as, member_user, private_scene,
):
    """The level for everyone limits every account that is neither owner nor admin."""
    _set_mode(http_client, private_scene.db, "everyone", level="read")
    login_as(member_user)
    assert http_client.get(f"{_PREFIX}/{private_scene.db}/schemas").status_code == 200
    resp = http_client.post(f"{_PREFIX}/{private_scene.db}/entries")
    assert resp.status_code == 403
    assert resp.json()["detail"] == READ_ONLY_DETAIL


def test_stranger_gets_the_no_access_detail_on_a_change(
    http_client, login_as, member_user, private_scene,
):
    login_as(member_user)
    resp = http_client.post(f"{_PREFIX}/{private_scene.db}/entries")
    assert resp.status_code == 403
    assert resp.json()["detail"] == NO_ACCESS_DETAIL


def test_reader_gets_the_entry_listings(http_client, login_as, member_user, private_scene):
    """The listings relation pickers call show a readable database in full."""
    _set_mode(http_client, private_scene.db, "whitelist", [str(member_user.id)], level="read")
    login_as(member_user)
    rows = http_client.get(f"{_PREFIX}/{private_scene.db}/entries").json()
    assert [row["id"] for row in rows] == [private_scene.entry]


# ─── Refused writes leave no trace ───────────────────────────────────────────


def test_refused_schema_delete_keeps_the_property_and_its_values(
    http_client, login_as, admin_user, member_user, private_scene,
):
    login_as(member_user)
    resp = http_client.delete(f"{_PREFIX}/{private_scene.db}/schemas/{private_scene.schema}")
    assert resp.status_code == 403

    login_as(admin_user)
    entries = http_client.get(f"{_PREFIX}/{private_scene.db}/entries").json()
    assert entries[0]["values"][private_scene.schema] == {"text": "Value A"}


def test_refused_value_write_keeps_the_stored_value(
    http_client, login_as, admin_user, member_user, private_scene,
):
    login_as(member_user)
    resp = _put_value(http_client, private_scene.db, private_scene.entry, private_scene.schema, {"text": "Value B"})
    assert resp.status_code == 403

    login_as(admin_user)
    entries = http_client.get(f"{_PREFIX}/{private_scene.db}/entries").json()
    assert entries[0]["values"][private_scene.schema] == {"text": "Value A"}


def test_refused_schema_create_adds_nothing(http_client, login_as, admin_user, member_user, private_scene):
    login_as(member_user)
    resp = http_client.post(
        f"{_PREFIX}/{private_scene.db}/schemas", json={"name": "Field B", "type": "text"},
    )
    assert resp.status_code == 403

    login_as(admin_user)
    names = [s["name"] for s in http_client.get(f"{_PREFIX}/{private_scene.db}/schemas").json()]
    assert "Field B" not in names


# ─── Non-database blocks ─────────────────────────────────────────────────────


def test_member_cannot_tell_a_private_page_from_a_missing_database(
    http_client, login_as, member_user,
):
    """
    An admin gets 409 for a block that is not a database. A member without
    access gets the 404 of a missing database instead, so the answer does not
    reveal that a block with that id exists.
    """
    resp = http_client.post("/api/blocks", json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)})
    page_id = resp.json()["id"]
    _set_mode(http_client, page_id, "private")
    assert http_client.get(f"{_PREFIX}/{page_id}/schemas").status_code == 409

    login_as(member_user)
    assert http_client.get(f"{_PREFIX}/{page_id}/schemas").status_code == 404
    assert http_client.get(f"{_PREFIX}/{uuid.uuid4()}/schemas").status_code == 404


# ─── GET /api/databases ──────────────────────────────────────────────────────


def test_database_list_leaves_out_databases_the_member_may_not_access(
    http_client, login_as, member_user,
):
    open_db = _create_database(http_client, title="Open X")
    private_db = _create_database(http_client, title="Private X")
    _set_mode(http_client, private_db, "private")

    assert {d["id"] for d in http_client.get(_PREFIX).json()} == {open_db, private_db}

    login_as(member_user)
    listed = http_client.get(_PREFIX).json()
    assert [d["id"] for d in listed] == [open_db]
    assert all(d["title"] != "Private X" for d in listed)


def test_database_list_includes_a_database_the_member_is_granted(
    http_client, login_as, member_user,
):
    granted = _create_database(http_client)
    _set_mode(http_client, granted, "whitelist", [str(member_user.id)])

    login_as(member_user)
    assert [d["id"] for d in http_client.get(_PREFIX).json()] == [granted]


# ─── Relation targets ────────────────────────────────────────────────────────


@dataclass
class RelationScene:
    """An open database whose relation points into a private one."""

    open_db: str
    private_db: str
    relation: str
    private_entry: str


@pytest.fixture
def relation_scene(http_client) -> RelationScene:
    open_db = _create_database(http_client, title="Open X")
    private_db = _create_database(http_client, title="Private X")
    relation = _create_schema(
        http_client, open_db, "Link A", "relation", _relation_config(private_db),
    )
    private_entry = _create_entry(http_client, private_db)
    _set_mode(http_client, private_db, "private")
    return RelationScene(open_db, private_db, relation["id"], private_entry)


def test_member_cannot_create_a_relation_into_a_private_database(
    http_client, login_as, admin_user, member_user, relation_scene,
):
    login_as(member_user)
    resp = http_client.post(
        f"{_PREFIX}/{relation_scene.open_db}/schemas",
        json={
            "name": "Link B", "type": "relation",
            "config": _relation_config(relation_scene.private_db, mirror="Back B"),
        },
    )
    assert resp.status_code == 403

    login_as(admin_user)
    names = [s["name"] for s in http_client.get(f"{_PREFIX}/{relation_scene.private_db}/schemas").json()]
    assert "Back B" not in names
    names = [s["name"] for s in http_client.get(f"{_PREFIX}/{relation_scene.open_db}/schemas").json()]
    assert "Link B" not in names


def test_member_can_create_a_relation_between_accessible_databases(
    http_client, login_as, member_user,
):
    source = _create_database(http_client)
    target = _create_database(http_client)

    login_as(member_user)
    resp = http_client.post(
        f"{_PREFIX}/{source}/schemas",
        json={"name": "Link A", "type": "relation", "config": _relation_config(target)},
    )
    assert resp.status_code == 201


def test_member_can_create_a_self_relation(http_client, login_as, member_user):
    source = _create_database(http_client)

    login_as(member_user)
    resp = http_client.post(
        f"{_PREFIX}/{source}/schemas",
        json={
            "name": "Link A", "type": "relation",
            "config": {"target_database_id": source, "direction": "bilateral_self"},
        },
    )
    assert resp.status_code == 201


def test_member_cannot_rename_a_relation_into_a_private_database(
    http_client, login_as, member_user, relation_scene,
):
    login_as(member_user)
    resp = http_client.patch(
        f"{_PREFIX}/{relation_scene.open_db}/schemas/{relation_scene.relation}",
        json={"name": "Link C"},
    )
    assert resp.status_code == 403


def test_member_cannot_retarget_a_relation_to_a_private_database(
    http_client, login_as, member_user,
):
    source = _create_database(http_client)
    other = _create_database(http_client)
    private_db = _create_database(http_client)
    relation = _create_schema(
        http_client, source, "Link A", "relation", _relation_config(other, direction="unilateral"),
    )
    _set_mode(http_client, private_db, "private")

    login_as(member_user)
    resp = http_client.patch(
        f"{_PREFIX}/{source}/schemas/{relation['id']}",
        json={"config": _relation_config(private_db, direction="unilateral")},
    )
    assert resp.status_code == 403


def test_member_cannot_turn_a_property_into_a_relation_into_a_private_database(
    http_client, login_as, member_user,
):
    source = _create_database(http_client)
    private_db = _create_database(http_client)
    field = _create_schema(http_client, source, "Field A", "text")
    _set_mode(http_client, private_db, "private")

    login_as(member_user)
    resp = http_client.patch(
        f"{_PREFIX}/{source}/schemas/{field['id']}",
        json={"type": "relation", "config": _relation_config(private_db)},
    )
    assert resp.status_code == 403


def test_member_may_move_a_relation_into_a_private_database(
    http_client, login_as, member_user, relation_scene,
):
    """Position and group stay within the open database and touch no mirror."""
    login_as(member_user)
    resp = http_client.patch(
        f"{_PREFIX}/{relation_scene.open_db}/schemas/{relation_scene.relation}",
        json={"position": 42.0, "group": "Group A"},
    )
    assert resp.status_code == 200


def test_member_may_delete_a_relation_into_a_private_database(
    http_client, login_as, admin_user, member_user, relation_scene,
):
    """The delete removes the property here and leaves the mirror untouched."""
    login_as(member_user)
    resp = http_client.delete(f"{_PREFIX}/{relation_scene.open_db}/schemas/{relation_scene.relation}")
    assert resp.status_code == 204

    login_as(admin_user)
    names = [s["name"] for s in http_client.get(f"{_PREFIX}/{relation_scene.private_db}/schemas").json()]
    assert "Back A" in names


def test_member_cannot_link_entries_of_a_private_database(
    http_client, login_as, admin_user, member_user, relation_scene,
):
    entry = _create_entry(http_client, relation_scene.open_db)

    login_as(member_user)
    resp = _put_value(
        http_client, relation_scene.open_db, entry, relation_scene.relation,
        {"related_ids": [relation_scene.private_entry]},
    )
    assert resp.status_code == 403

    login_as(admin_user)
    mirror_id = next(
        s["id"] for s in http_client.get(f"{_PREFIX}/{relation_scene.private_db}/schemas").json()
        if s["name"] == "Back A"
    )
    private_rows = http_client.get(f"{_PREFIX}/{relation_scene.private_db}/entries").json()
    assert all(not row["values"].get(mirror_id) for row in private_rows)
    open_rows = http_client.get(f"{_PREFIX}/{relation_scene.open_db}/entries").json()
    assert all(not row["values"].get(relation_scene.relation) for row in open_rows)


def test_member_cannot_clear_a_link_into_a_private_database(
    http_client, login_as, member_user, relation_scene,
):
    """Clearing a bilateral link removes the back-reference in the target too."""
    entry = _create_entry(http_client, relation_scene.open_db)
    assert _put_value(
        http_client, relation_scene.open_db, entry, relation_scene.relation,
        {"related_ids": [relation_scene.private_entry]},
    ).status_code == 200

    login_as(member_user)
    resp = _put_value(http_client, relation_scene.open_db, entry, relation_scene.relation, None)
    assert resp.status_code == 403


def test_member_cannot_duplicate_an_entry_linked_into_a_private_database(
    http_client, login_as, admin_user, member_user, relation_scene,
):
    entry = _create_entry(http_client, relation_scene.open_db)
    assert _put_value(
        http_client, relation_scene.open_db, entry, relation_scene.relation,
        {"related_ids": [relation_scene.private_entry]},
    ).status_code == 200

    login_as(member_user)
    resp = http_client.post(f"{_PREFIX}/{relation_scene.open_db}/entries/{entry}/duplicate")
    assert resp.status_code == 403

    login_as(admin_user)
    assert len(http_client.get(f"{_PREFIX}/{relation_scene.open_db}/entries").json()) == 1


def test_member_may_duplicate_an_entry_without_a_link_into_a_private_database(
    http_client, login_as, member_user, relation_scene,
):
    entry = _create_entry(http_client, relation_scene.open_db)

    login_as(member_user)
    resp = http_client.post(f"{_PREFIX}/{relation_scene.open_db}/entries/{entry}/duplicate")
    assert resp.status_code == 201


def test_member_cannot_apply_a_template_linked_into_a_private_database(
    http_client, login_as, member_user, relation_scene,
):
    template = _create_template(http_client, relation_scene.open_db)
    assert _put_value(
        http_client, relation_scene.open_db, template, relation_scene.relation,
        {"related_ids": [relation_scene.private_entry]},
    ).status_code == 200
    entry = _create_entry(http_client, relation_scene.open_db)

    login_as(member_user)
    resp = http_client.post(
        f"{_PREFIX}/{relation_scene.open_db}/entry-templates/{template}/apply/{entry}",
    )
    assert resp.status_code == 403


def test_member_may_link_once_granted_the_target(
    http_client, login_as, member_user, relation_scene,
):
    entry = _create_entry(http_client, relation_scene.open_db)
    _set_mode(http_client, relation_scene.private_db, "whitelist", [str(member_user.id)])

    login_as(member_user)
    resp = _put_value(
        http_client, relation_scene.open_db, entry, relation_scene.relation,
        {"related_ids": [relation_scene.private_entry]},
    )
    assert resp.status_code == 200


def test_reading_the_target_is_not_enough_to_link_into_it(
    http_client, login_as, member_user, relation_scene,
):
    """A bilateral link writes a back-reference into the target's entry."""
    entry = _create_entry(http_client, relation_scene.open_db)
    _set_mode(
        http_client, relation_scene.private_db, "whitelist", [str(member_user.id)], level="read",
    )

    login_as(member_user)
    resp = _put_value(
        http_client, relation_scene.open_db, entry, relation_scene.relation,
        {"related_ids": [relation_scene.private_entry]},
    )
    assert resp.status_code == 403


# ─── Key references ──────────────────────────────────────────────────────────


def test_key_references_hide_the_title_of_an_inaccessible_database(
    http_client, login_as, member_user,
):
    keyed_db = _create_database(http_client, title="Open X")
    private_db = _create_database(http_client, title="Private X")
    key_field = _create_schema(http_client, keyed_db, "Date A", "date")
    _create_schema(
        http_client, private_db, "Link A", "relation",
        {
            "target_database_id": keyed_db,
            "direction": "unilateral",
            "keying": {"enabled": True, "key_property_id": key_field["id"]},
        },
    )
    _set_mode(http_client, private_db, "private")

    url = f"{_PREFIX}/{keyed_db}/schemas/{key_field['id']}/key-references"
    assert http_client.get(url).json()[0]["database_title"] == "Private X"

    login_as(member_user)
    references = http_client.get(url).json()
    assert len(references) == 1
    assert references[0]["database_title"] is None
