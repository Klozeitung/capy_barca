"""
Tests for the family tree endpoints against the PostgreSQL test database
(``http_client`` acts as an admin): drawing, the sibling mapper, and what
happens to a derived link a person changes by hand.
"""
import uuid

import pytest
from sqlalchemy import select

import app.database.database as db_module
from app.blocks.models import WORKSPACE_ROOT_ID, PropertyValue


def _database(client) -> str:
    resp = client.post("/api/blocks", json={"type": "database", "parent_id": str(WORKSPACE_ROOT_ID)})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


class Tree:
    def __init__(self, client):
        self.client = client
        self.db = _database(client)
        resp = client.post(
            f"/api/databases/{self.db}/views", json={"name": "Tree", "view_type": "family_tree"},
        )
        assert resp.status_code == 201, resp.text
        self.view = resp.json()["id"]
        self.schemas = {
            s["config"].get("kinRole"): s
            for s in client.get(f"/api/databases/{self.db}/schemas").json()
            if s["config"]
        }

    def entry(self) -> str:
        return self.client.post(f"/api/databases/{self.db}/entries").json()["id"]

    def put(self, entry, role, value):
        resp = self.client.put(
            f"/api/databases/{self.db}/entries/{entry}/values/{self.schemas[role]['id']}",
            json={"value": value},
        )
        assert resp.status_code == 200, resp.text
        return resp

    def parents(self, child, *parents, nuance="kin.parent.biological"):
        self.put(child, "parents", {
            "relationPool": {p: [""] for p in parents},
            "nuancePool": {p: {"": nuance} for p in parents},
        })

    def settings(self, **settings):
        resp = self.client.patch(
            f"/api/databases/{self.db}/views/{self.view}", json={"settings": settings},
        )
        assert resp.status_code == 200, resp.text

    def stored(self, entry, role):
        with db_module.SessionLocal() as db:
            return db.scalar(select(PropertyValue.value).where(
                PropertyValue.page_id == uuid.UUID(entry),
                PropertyValue.property_schema_id == uuid.UUID(self.schemas[role]["id"]),
            ))

    def draw(self, settings=None):
        body = {"settings": settings} if settings is not None else None
        return self.client.post(f"/api/databases/{self.db}/views/{self.view}/family-tree", json=body)

    def map(self, settings=None):
        body = {"settings": settings} if settings is not None else None
        return self.client.post(f"/api/databases/{self.db}/views/{self.view}/family-tree/map", json=body)


@pytest.fixture
def tree(http_client):
    return Tree(http_client)


def test_the_tree_draws_generations_around_the_root(tree):
    parent, child, other = tree.entry(), tree.entry(), tree.entry()
    tree.parents(child, parent)
    assert tree.draw().json()["root_valid"] is False

    tree.settings(familyTreeRootId=child)
    body = tree.draw().json()
    assert body["root_valid"] is True
    generations = {n["id"]: n["generation"] for n in body["nodes"]}
    assert generations == {parent: -1, child: 0}
    assert other not in generations
    [edge] = body["parent_edges"]
    assert (edge["parent"], edge["child"], edge["anchor"]) == (parent, child, True)
    assert edge["segments"][0]["base"] == "kin.parent.biological"


def test_the_endpoints_need_a_family_tree_view_and_mapping_a_root(tree, http_client):
    table = http_client.post(f"/api/databases/{tree.db}/views", json={"name": "Table"}).json()["id"]
    resp = http_client.post(f"/api/databases/{tree.db}/views/{table}/family-tree")
    assert resp.status_code == 404
    assert tree.map().status_code == 422


def test_view_settings_validate_the_family_tree_keys(tree):
    resp = tree.client.patch(
        f"/api/databases/{tree.db}/views/{tree.view}", json={"settings": {"familyTreeScope": "everyone"}},
    )
    assert resp.status_code == 422
    resp = tree.client.patch(
        f"/api/databases/{tree.db}/views/{tree.view}", json={"settings": {"familyTreeAt": "2000-07-28T12:00"}},
    )
    assert resp.status_code == 200, resp.text


def test_the_mapper_writes_derived_siblings_on_both_sides(tree):
    parent_a, parent_b, child_a, child_b = (tree.entry() for _ in range(4))
    tree.parents(child_a, parent_a, parent_b)
    tree.parents(child_b, parent_a, parent_b)
    tree.settings(familyTreeRootId=child_a)

    resp = tree.map()
    assert resp.status_code == 200, resp.text
    assert resp.json()["added"] == 1
    stored = tree.stored(child_a, "siblings")
    assert stored["relationPool"] == {child_b: [""]}
    assert stored["nuancePool"] == {child_b: {"": "kin.sibling.biological"}}
    assert stored["derived"] == [child_b]
    assert tree.stored(child_b, "siblings")["derived"] == [child_a]

    # A second run finds nothing to do.
    assert tree.map().json()["added"] == 0


def test_a_derived_link_changed_by_hand_becomes_manual_on_both_sides(tree):
    parent_a, parent_b, child_a, child_b = (tree.entry() for _ in range(4))
    tree.parents(child_a, parent_a, parent_b)
    tree.parents(child_b, parent_a, parent_b)
    tree.settings(familyTreeRootId=child_a)
    tree.map()

    # Saving the value unchanged keeps it derived, whatever the client sends.
    tree.put(child_a, "siblings", {
        "relationPool": {child_b: [""]}, "nuancePool": {child_b: {"": "kin.sibling.biological"}},
        "derived": [],
    })
    assert tree.stored(child_a, "siblings")["derived"] == [child_b]

    tree.put(child_a, "siblings", {
        "relationPool": {child_b: ["2000-07-28T00:00:00→"]},
        "nuancePool": {child_b: {"2000-07-28T00:00:00→": "kin.sibling.biological"}},
    })
    assert "derived" not in tree.stored(child_a, "siblings")
    assert "derived" not in tree.stored(child_b, "siblings")

    # The mapper now checks the manual link instead of overwriting it.
    result = tree.map().json()
    assert result["added"] == 0 and result["updated"] == 0
    assert tree.stored(child_a, "siblings")["relationPool"] == {child_b: ["2000-07-28T00:00:00→"]}


def test_a_derived_link_removed_by_hand_comes_back(tree):
    parent, child_a, child_b = (tree.entry() for _ in range(3))
    tree.parents(child_a, parent)
    tree.parents(child_b, parent)
    tree.settings(familyTreeRootId=parent)
    tree.map()
    tree.put(child_a, "siblings", None)
    assert tree.map().json()["added"] == 1
    assert tree.stored(child_b, "siblings")["derived"] == [child_a]


def test_the_mapper_lists_contradictions_with_titles(tree):
    mother_a, mother_b, child = (tree.entry() for _ in range(3))
    sex = tree.schemas["sex"]
    female = sex["config"]["kinFemaleOptionId"]
    for mother in (mother_a, mother_b):
        tree.put(mother, "sex", {"option": female})
    tree.parents(child, mother_a, mother_b)
    tree.settings(familyTreeRootId=child)

    body = tree.map().json()
    assert body["conflicts"] == [{"kind": "two_mothers", "entries": [child, mother_a, mother_b]}]
    assert set(body["entries"]) == {child, mother_a, mother_b}
    drawn = {n["id"]: n for n in tree.draw().json()["nodes"]}
    assert drawn[child]["warnings"] == ["two_mothers"]


def test_overlays_and_the_property_line_come_with_the_tree(tree, http_client):
    friends = http_client.post(f"/api/databases/{tree.db}/schemas", json={
        "name": "Relation A", "type": "relation",
        "config": {"target_database_id": tree.db, "direction": "bilateral_self"},
    }).json()
    text = http_client.post(f"/api/databases/{tree.db}/schemas", json={"name": "Text A", "type": "text"}).json()
    parent, child, outsider = (tree.entry() for _ in range(3))
    tree.parents(child, parent)
    http_client.put(
        f"/api/databases/{tree.db}/entries/{child}/values/{friends['id']}",
        json={"value": {"related_ids": [outsider]}},
    )
    http_client.put(
        f"/api/databases/{tree.db}/entries/{child}/values/{text['id']}", json={"value": {"text": "Value A"}},
    )
    tree.settings(
        familyTreeRootId=child,
        familyTreeProperties=[text["id"]],
        familyTreeOverlays=[
            {"schemaId": friends["id"], "direction": "both", "showOutsiders": True},
            {"schemaId": str(uuid.uuid4())},
        ],
    )
    body = tree.draw().json()
    nodes = {n["id"]: n for n in body["nodes"]}
    assert nodes[outsider]["outsider"] is True
    assert nodes[child]["properties"] == [{"schema_id": text["id"], "name": "Text A", "tokens": [{"text": "Value A"}], "slots": None}]
    valid, invalid = body["overlays"]
    assert valid["valid"] is True and len(valid["edges"]) == 1
    assert invalid["valid"] is False

    http_client.delete(f"/api/databases/{tree.db}/schemas/{text['id']}")
    view = next(v for v in http_client.get(f"/api/databases/{tree.db}/views").json()["views"] if v["id"] == tree.view)
    assert view["settings"]["familyTreeProperties"] == []


def test_settings_sent_with_the_request_win_over_the_stored_ones(tree, http_client):
    text = http_client.post(f"/api/databases/{tree.db}/schemas", json={"name": "Text A", "type": "text"}).json()
    parent, child = tree.entry(), tree.entry()
    tree.parents(child, parent)
    http_client.put(
        f"/api/databases/{tree.db}/entries/{child}/values/{text['id']}", json={"value": {"text": "Value A"}},
    )
    tree.settings(familyTreeRootId=parent)

    # Not saved yet: the request carries the settings the client shows.
    body = tree.draw({"familyTreeRootId": child, "familyTreeProperties": [text["id"]]}).json()
    nodes = {n["id"]: n for n in body["nodes"]}
    assert body["root"] == child
    assert nodes[child]["properties"][0]["tokens"] == [{"text": "Value A"}]
    # Keys left out are unset, the stored root does not come back.
    assert tree.draw({}).json()["root_valid"] is False
    # Nothing was stored.
    assert tree.draw().json()["root"] == parent


def test_settings_in_the_request_are_validated(tree):
    assert tree.draw({"colOrder": []}).status_code == 422
    assert tree.draw({"familyTreeScope": "everyone"}).status_code == 422
    assert tree.map({}).status_code == 422


def test_the_mapper_works_with_the_settings_of_the_request(tree):
    parent, child_a, child_b = (tree.entry() for _ in range(3))
    tree.parents(child_a, parent)
    tree.parents(child_b, parent)
    resp = tree.map({"familyTreeRootId": parent})
    assert resp.status_code == 200, resp.text
    assert resp.json()["added"] == 1
