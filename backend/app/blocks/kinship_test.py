"""
Tests for the family set (``app.blocks.kinship``).

The rules are tested as pure functions first; the guards, the system nuances
and the completion by a family tree view run through the HTTP layer against
the PostgreSQL test database (``http_client`` acts as an admin).
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

import app.database.database as db_module
from app.blocks import kinship
from app.blocks import repository as repo
from app.blocks.models import WORKSPACE_ROOT_ID, PropertyValue

DB = uuid.uuid4()


def _schema(name, type_="relation", config=None, database_id=DB, schema_id=None):
    return SimpleNamespace(
        id=schema_id or uuid.uuid4(), database_id=database_id, name=name,
        type=type_, config=config,
    )


def _parents_config(mirror="Kinder", target=DB):
    return {"target_database_id": str(target), "direction": "bilateral", "mirror_property_name": mirror}


def _self_config(target=DB):
    return {"target_database_id": str(target), "direction": "bilateral_self"}


# ─── Names and languages ─────────────────────────────────────────────────────


@pytest.mark.parametrize("name, role", [
    ("Eltern", kinship.PARENTS),
    ("  parents ", kinship.PARENTS),
    ("KINDER", kinship.CHILDREN),
    ("Half-siblings", None),
    ("geschlecht (biologisch)", kinship.SEX),
    ("Time of death", kinship.DEATH),
    ("Parent", None),
    ("", None),
    (None, None),
])
def test_role_for_name_ignores_case_and_surrounding_spaces(name, role):
    assert kinship.role_for_name(name) == role


@pytest.mark.parametrize("user, header, language", [
    ("de", "en-US,en;q=0.9", "de"),
    (None, "de-DE,de;q=0.9,en;q=0.8", "de"),
    (None, "fr-FR,en;q=0.5", "en"),
    (None, None, "en"),
    ("xx", "fr", "en"),
])
def test_language_for_prefers_the_account_then_the_header(user, header, language):
    assert kinship.language_for(user, header) == language


# ─── Structure ───────────────────────────────────────────────────────────────


def test_parents_fit_a_bilateral_relation_to_the_database_mirrored_as_children():
    assert kinship.fits(kinship.PARENTS, "relation", _parents_config(), DB)
    assert kinship.fits(kinship.PARENTS, "relation", _parents_config("Children"), DB)
    assert not kinship.fits(kinship.PARENTS, "relation", _parents_config("Other"), DB)
    assert not kinship.fits(kinship.PARENTS, "relation", _parents_config(target=uuid.uuid4()), DB)
    assert not kinship.fits(kinship.PARENTS, "relation", _self_config(), DB)
    assert not kinship.fits(kinship.PARENTS, "text", _parents_config(), DB)


def test_self_roles_fit_a_relation_of_the_database_to_itself():
    for role in kinship.SELF_ROLES:
        assert kinship.fits(role, "relation", _self_config(), DB)
        assert not kinship.fits(role, "relation", _parents_config(), DB)


def test_date_roles_take_dates_and_computed_dates_and_sex_takes_a_select():
    for type_ in ("date", "rollup", "formula"):
        assert kinship.fits(kinship.BIRTH, type_, {}, DB)
    assert not kinship.fits(kinship.DEATH, "text", {}, DB)
    assert kinship.fits(kinship.SEX, "select", {"options": []}, DB)
    assert not kinship.fits(kinship.SEX, "multi_select", {}, DB)


# ─── Markers ─────────────────────────────────────────────────────────────────


def test_marked_config_gives_a_fitting_property_its_role():
    schema = _schema("Eltern", config=_parents_config())
    assert kinship.marked_config(schema, [])[kinship.ROLE_KEY] == kinship.PARENTS


def test_marked_config_leaves_an_unfitting_property_unmarked():
    schema = _schema("Eltern", "text", {kinship.ROLE_KEY: kinship.PARENTS})
    assert kinship.marked_config(schema, []) == {}


def test_marked_config_gives_each_role_to_one_property_per_database():
    holder = _schema("Parents", config={**_parents_config("Children"), kinship.ROLE_KEY: kinship.PARENTS})
    second = _schema("Eltern", config=_parents_config())
    assert kinship.marked_config(second, [holder]) is None


def test_marked_config_returns_none_when_nothing_changes():
    schema = _schema("Partner", config={
        **_self_config(), kinship.ROLE_KEY: kinship.PARTNERS, "nuance": {"enabled": True},
    })
    assert kinship.marked_config(schema, []) is None


def test_marked_config_switches_the_nuance_of_relation_roles_on():
    schema = _schema("Geschwister", config={**_self_config(), "nuance": {"enabled": False, "affix1": "x"}})
    marked = kinship.marked_config(schema, [])
    assert marked["nuance"] == {"enabled": True, "affix1": "x"}
    birth = _schema("Geburtsdatum", "date", {})
    assert "nuance" not in kinship.marked_config(birth, [])


def test_sex_points_at_the_female_and_male_options_and_keeps_them_while_they_exist():
    options = [{"id": "o-1", "label": "Male"}, {"id": "o-2", "label": " Female "}, {"id": "o-4", "label": "Other"}]
    schema = _schema("Sex (biological)", "select", {"options": options})
    marked = kinship.marked_config(schema, [])
    assert marked[kinship.FEMALE_OPTION_KEY] == "o-2"
    assert marked[kinship.MALE_OPTION_KEY] == "o-1"

    kept = _schema("Sex (biological)", "select", {**marked, "options": [*options, {"id": "o-3", "label": "weiblich"}]})
    kept.config[kinship.FEMALE_OPTION_KEY] = "o-4"
    assert kinship.marked_config(kept, []) is None


def test_the_male_option_is_never_the_female_one():
    options = [{"id": "o-1", "label": "female"}]
    schema = _schema("Sex (biological)", "select", {"options": options, kinship.MALE_OPTION_KEY: "o-1"})
    marked = kinship.marked_config(schema, [])
    assert marked[kinship.FEMALE_OPTION_KEY] == "o-1"
    assert kinship.MALE_OPTION_KEY not in marked


def test_keep_server_owned_keys_drops_the_clients_markers():
    old = {kinship.ROLE_KEY: kinship.SEX, kinship.FEMALE_OPTION_KEY: "o-1"}
    new = {"options": [], kinship.ROLE_KEY: kinship.PARENTS, kinship.FEMALE_OPTION_KEY: "o-9"}
    assert kinship.keep_server_owned_keys(old, new) == {
        "options": [], kinship.ROLE_KEY: kinship.SEX, kinship.FEMALE_OPTION_KEY: "o-1",
    }
    assert kinship.keep_server_owned_keys(None, new) == {"options": []}


def test_strip_markers_leaves_other_keys():
    assert kinship.strip_markers({"a": 1, kinship.ROLE_KEY: kinship.BIRTH}) == {"a": 1}
    assert kinship.strip_markers(None) is None


# ─── System nuances ──────────────────────────────────────────────────────────


def test_catalogue_lists_base_nuances_in_the_language_then_aliases():
    aliases = [
        {"id": "a-1", "group": kinship.GROUP_PARENTS, "base": "kin.parent.adoptive", "label": "Label A"},
        {"id": "a-2", "group": kinship.GROUP_SIBLINGS, "base": "kin.sibling.half", "label": "Label B"},
    ]
    catalogue = kinship.catalogue(aliases, "de")
    assert catalogue[kinship.GROUP_PARENTS][0] == {"id": "kin.parent.biological", "label": "leiblich"}
    assert catalogue[kinship.GROUP_PARENTS][-1] == {"id": "a-1", "label": "Label A"}
    assert [o["id"] for o in catalogue[kinship.GROUP_SIBLINGS]] == [
        *kinship.base_ids(kinship.GROUP_SIBLINGS), "a-2",
    ]
    assert catalogue[kinship.GROUP_SIBLINGS][1]["label"] == "halb"
    assert set(catalogue) == {kinship.GROUP_PARENTS, kinship.GROUP_PARTNERS, kinship.GROUP_SIBLINGS}
    assert kinship.catalogue([], "en")[kinship.GROUP_PARTNERS][0]["label"] == "marriage"


def test_base_of_resolves_aliases_to_their_base():
    aliases = [{"id": "a-1", "group": kinship.GROUP_SIBLINGS, "base": "kin.sibling.step", "label": "Label A"}]
    assert kinship.base_of("kin.parent.legal", aliases) == "kin.parent.legal"
    assert kinship.base_of("a-1", aliases) == "kin.sibling.step"
    assert kinship.base_of("unknown", aliases) is None
    assert kinship.base_of(None, aliases) is None


def test_with_system_nuances_fills_options_of_marked_relations_only():
    catalogue = kinship.catalogue([], "en")
    marked = {**_self_config(), kinship.ROLE_KEY: kinship.PARTNERS, "nuance": {"enabled": True, "affix1": "x"}}
    shown = kinship.with_system_nuances(marked, catalogue)
    assert shown["nuance"]["enabled"] is True
    assert shown["nuance"]["affix1"] == "x"
    assert [o["id"] for o in shown["nuance"]["options"]][0] == "kin.partner.marriage"
    assert "options" not in marked["nuance"]

    without_nuance = kinship.with_system_nuances({kinship.ROLE_KEY: kinship.CHILDREN}, catalogue)
    assert without_nuance["nuance"]["enabled"] is False
    assert without_nuance["nuance"]["options"][0]["id"] == "kin.parent.biological"

    plain = {"direction": "unilateral"}
    assert kinship.with_system_nuances(plain, catalogue) is plain
    assert kinship.with_system_nuances({kinship.ROLE_KEY: kinship.BIRTH}, catalogue) == {kinship.ROLE_KEY: kinship.BIRTH}
    assert kinship.with_system_nuances(marked, None) is marked


def test_without_client_nuance_options_applies_to_reserved_relation_names():
    config = {"nuance": {"enabled": True, "options": [{"id": "x", "label": "Label A"}]}}
    assert kinship.without_client_nuance_options("Geschwister", "relation", config)["nuance"]["options"] == []
    assert kinship.without_client_nuance_options("Other", "relation", config) is config
    assert kinship.without_client_nuance_options("Geburtsdatum", "relation", config) is config


def test_normalize_aliases_checks_groups_bases_and_labels():
    result = kinship.normalize_aliases([
        {"group": "parents", "base": "kin.parent.foster", "label": " Label A "},
        {"group": "siblings", "base": "kin.sibling.half", "label": "Label B"},
    ], [])
    assert [(a["group"], a["base"], a["label"]) for a in result] == [
        ("parents", "kin.parent.foster", "Label A"), ("siblings", "kin.sibling.half", "Label B"),
    ]
    assert all(uuid.UUID(a["id"]) for a in result)


@pytest.mark.parametrize("item", [
    {"group": "cousins", "base": None, "label": "Label A"},
    {"group": "parents", "base": None, "label": "Label A"},
    {"group": "parents", "base": "kin.partner.marriage", "label": "Label A"},
    {"group": "siblings", "base": "kin.parent.legal", "label": "Label A"},
    {"group": "siblings", "base": None, "label": "Label A"},
    {"group": "half_siblings", "base": None, "label": "Label A"},
    {"group": "siblings", "base": "kin.sibling.step", "label": "Halb"},
    {"group": "parents", "base": "kin.parent.legal", "label": "  "},
    {"group": "parents", "base": "kin.parent.legal", "label": "Adoptive"},
])
def test_normalize_aliases_refuses_invalid_items(item):
    with pytest.raises(HTTPException) as exc:
        kinship.normalize_aliases([item], [])
    assert exc.value.status_code == 422


def test_normalize_aliases_keeps_ids_and_refuses_duplicates_and_group_moves():
    previous = [{"id": "a-1", "group": "parents", "base": "kin.parent.step", "label": "Label A"}]
    kept = kinship.normalize_aliases(
        [{"id": "a-1", "group": "parents", "base": "kin.parent.legal", "label": "Label C"}], previous,
    )
    assert kept[0]["id"] == "a-1"
    with pytest.raises(HTTPException):
        kinship.normalize_aliases([{"id": "a-1", "group": "partners", "base": "kin.partner.affair", "label": "X"}], previous)
    with pytest.raises(HTTPException):
        kinship.normalize_aliases([
            {"group": "siblings", "base": "kin.sibling.step", "label": "Label A"},
            {"group": "siblings", "base": "kin.sibling.step", "label": "label a"},
        ], [])


def test_replacements_map_removed_aliases_to_their_base():
    previous = [
        {"id": "a-1", "group": "parents", "base": "kin.parent.step", "label": "Label A"},
        {"id": "a-2", "group": "siblings", "base": "kin.sibling.foster", "label": "Label B"},
        {"id": "a-3", "group": "parents", "base": "kin.parent.legal", "label": "Label C"},
    ]
    assert kinship.replacements_for_removed(previous, previous[2:]) == {
        "parents": {"a-1": "kin.parent.step"}, "siblings": {"a-2": "kin.sibling.foster"},
    }


# ─── Through the API ─────────────────────────────────────────────────────────


def _database(client) -> str:
    resp = client.post("/api/blocks", json={"type": "database", "parent_id": str(WORKSPACE_ROOT_ID)})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _create(client, database_id, name, type_, config=None):
    body = {"name": name, "type": type_}
    if config is not None:
        body["config"] = config
    return client.post(f"/api/databases/{database_id}/schemas", json=body)


def _schemas(client, database_id) -> dict:
    resp = client.get(f"/api/databases/{database_id}/schemas")
    assert resp.status_code == 200, resp.text
    return {s["name"]: s for s in resp.json()}


def _patch(client, database_id, schema_id, **body):
    return client.patch(f"/api/databases/{database_id}/schemas/{schema_id}", json=body)


def _family_tree(client, database_id, headers=None):
    resp = client.post(
        f"/api/databases/{database_id}/views",
        json={"name": "Tree", "view_type": "family_tree"},
        headers=headers or {},
    )
    return resp


def test_creating_parents_marks_both_sides_and_drops_client_nuance_options(http_client):
    db_id = _database(http_client)
    config = {**_parents_config(target=db_id), "nuance": {"enabled": True, "options": [{"label": "Label A"}]}}
    resp = _create(http_client, db_id, "Eltern", "relation", config)
    assert resp.status_code == 201, resp.text

    schemas = _schemas(http_client, db_id)
    assert schemas["Eltern"]["config"][kinship.ROLE_KEY] == kinship.PARENTS
    assert schemas["Kinder"]["config"][kinship.ROLE_KEY] == kinship.CHILDREN
    options = schemas["Eltern"]["config"]["nuance"]["options"]
    assert [o["id"] for o in options] == kinship.base_ids(kinship.GROUP_PARENTS)
    assert schemas["Kinder"]["config"]["nuance"]["options"][0]["label"] == "biological"


def test_system_nuances_follow_the_language(http_client, admin_user):
    db_id = _database(http_client)
    _create(http_client, db_id, "Partner", "relation", _self_config(db_id))
    resp = http_client.get(
        f"/api/databases/{db_id}/schemas", headers={"Accept-Language": "de-DE,de;q=0.9"},
    )
    partner = next(s for s in resp.json() if s["name"] == "Partner")
    assert partner["config"]["nuance"]["options"][0]["label"] == "Ehe"

    admin_user.language = "en"
    resp = http_client.get(
        f"/api/databases/{db_id}/schemas", headers={"Accept-Language": "de-DE"},
    )
    partner = next(s for s in resp.json() if s["name"] == "Partner")
    assert partner["config"]["nuance"]["options"][0]["label"] == "marriage"


def test_a_reserved_name_needs_the_structure_of_its_role(http_client):
    db_id = _database(http_client)
    resp = _create(http_client, db_id, "Geschwister", "text")
    assert resp.status_code == 422
    resp = _create(http_client, db_id, "Eltern", "relation", _parents_config("Other", db_id))
    assert resp.status_code == 422


def test_a_role_is_held_once_per_database(http_client):
    db_id = _database(http_client)
    assert _create(http_client, db_id, "Geburtsdatum", "date").status_code == 201
    resp = _create(http_client, db_id, "Date of birth", "date")
    assert resp.status_code == 409


def test_a_client_cannot_set_or_remove_markers(http_client):
    db_id = _database(http_client)
    resp = _create(http_client, db_id, "Other", "date", {kinship.ROLE_KEY: kinship.BIRTH})
    assert kinship.ROLE_KEY not in (resp.json()["config"] or {})

    birth = _create(http_client, db_id, "Geburtsdatum", "date").json()
    resp = _patch(http_client, db_id, birth["id"], config={"includeTime": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["config"][kinship.ROLE_KEY] == kinship.BIRTH


def test_a_marked_property_cannot_be_renamed_retyped_or_keyed(http_client):
    db_id = _database(http_client)
    birth = _create(http_client, db_id, "Geburtsdatum", "date").json()
    assert _patch(http_client, db_id, birth["id"], name="Other").status_code == 422
    assert _patch(http_client, db_id, birth["id"], type="text").status_code == 422

    partner = _create(http_client, db_id, "Partner", "relation", _self_config(db_id)).json()
    keyed = {**_self_config(db_id), "keying": {"enabled": True, "key_property_id": birth["id"]}}
    assert _patch(http_client, db_id, partner["id"], config=keyed).status_code == 422
    other_target = {**_self_config(db_id), "direction": "unilateral"}
    assert _patch(http_client, db_id, partner["id"], config=other_target).status_code == 422


def test_renaming_onto_a_reserved_name_marks_the_property(http_client):
    db_id = _database(http_client)
    created = _create(http_client, db_id, "Other", "date").json()
    resp = _patch(http_client, db_id, created["id"], name="Todeszeitpunkt")
    assert resp.status_code == 200, resp.text
    assert resp.json()["config"][kinship.ROLE_KEY] == kinship.DEATH

    text = _create(http_client, db_id, "Text A", "text").json()
    assert _patch(http_client, db_id, text["id"], name="Geschwister").status_code == 422
    # No longer reserved.
    assert _patch(http_client, db_id, text["id"], name="Halbgeschwister").status_code == 200


def test_the_options_marked_female_and_male_cannot_be_removed(http_client):
    db_id = _database(http_client)
    sex = _create(http_client, db_id, "Sex (biological)", "select", {
        "options": [{"label": "female"}, {"label": "male"}, {"label": "Option C"}],
    }).json()
    female = sex["config"][kinship.FEMALE_OPTION_KEY]
    male_id = sex["config"][kinship.MALE_OPTION_KEY]
    by_id = {o["id"]: o for o in sex["config"]["options"]}
    other = next(o for o in sex["config"]["options"] if o["id"] not in (female, male_id))
    resp = _patch(http_client, db_id, sex["id"], config={"options": [by_id[male_id], other]})
    assert resp.status_code == 422
    resp = _patch(http_client, db_id, sex["id"], config={"options": [by_id[female], other]})
    assert resp.status_code == 422
    renamed = [{"id": female, "label": "Label A"}, {"id": male_id, "label": "Label B"}]
    resp = _patch(http_client, db_id, sex["id"], config={"options": renamed})
    assert resp.status_code == 200, resp.text
    assert resp.json()["config"][kinship.FEMALE_OPTION_KEY] == female
    assert resp.json()["config"][kinship.MALE_OPTION_KEY] == male_id


def test_a_relation_of_the_set_is_nuanced_from_the_start_and_stays_so(http_client):
    db_id = _database(http_client)
    partner = _create(http_client, db_id, "Partner", "relation", _self_config(db_id)).json()
    assert partner["config"]["nuance"]["enabled"] is True
    resp = _patch(http_client, db_id, partner["id"], config={**_self_config(db_id), "nuance": {"enabled": False}})
    assert resp.status_code == 422
    resp = _patch(http_client, db_id, partner["id"], config={**_self_config(db_id)})
    assert resp.status_code == 422


def test_a_dormant_set_may_be_deleted_and_partners_switch_their_timeline_off(http_client):
    db_id = _database(http_client)
    partner = _create(http_client, db_id, "Partner", "relation", _self_config(db_id)).json()
    nuanced = {**_self_config(db_id), "nuance": {"enabled": True}}
    resp = _patch(http_client, db_id, partner["id"], config={**nuanced, "hasTimeline": True})
    assert resp.status_code == 200, resp.text
    resp = _patch(http_client, db_id, partner["id"], config={**nuanced, "hasTimeline": False})
    assert resp.status_code == 200, resp.text
    resp = http_client.delete(f"/api/databases/{db_id}/schemas/{partner['id']}")
    assert resp.status_code == 204


def _value(schema_id, entry_id):
    with db_module.SessionLocal() as db:
        return db.scalar(select(PropertyValue.value).where(
            PropertyValue.page_id == uuid.UUID(entry_id),
            PropertyValue.property_schema_id == uuid.UUID(schema_id),
        ))


def test_a_timeline_at_siblings_switches_parents_and_children_on(http_client):
    db_id = _database(http_client)
    _create(http_client, db_id, "Eltern", "relation", _parents_config(target=db_id))
    _create(http_client, db_id, "Partner", "relation", _self_config(db_id))
    siblings = _create(http_client, db_id, "Geschwister", "relation", _self_config(db_id)).json()
    schemas = _schemas(http_client, db_id)
    entry_a = http_client.post(f"/api/databases/{db_id}/entries").json()["id"]
    entry_b = http_client.post(f"/api/databases/{db_id}/entries").json()["id"]
    resp = http_client.put(
        f"/api/databases/{db_id}/entries/{entry_a}/values/{schemas['Eltern']['id']}",
        json={"value": {"related_ids": [entry_b]}},
    )
    assert resp.status_code == 200, resp.text

    resp = _patch(http_client, db_id, siblings["id"], config={**siblings["config"], "hasTimeline": True})
    assert resp.status_code == 200, resp.text
    schemas = _schemas(http_client, db_id)
    for name in ("Eltern", "Kinder", "Geschwister"):
        assert schemas[name]["config"]["hasTimeline"] is True, name
    assert not schemas["Partner"]["config"].get("hasTimeline")
    assert "_timeline" in _value(schemas["Eltern"]["id"], entry_a)
    assert "_timeline" in _value(schemas["Kinder"]["id"], entry_b)

    off = {**schemas["Kinder"]["config"], "hasTimeline": False}
    assert _patch(http_client, db_id, schemas["Kinder"]["id"], config=off).status_code == 422


def test_a_new_coupled_role_gets_the_timeline_of_the_others(http_client):
    db_id = _database(http_client)
    parents = _create(http_client, db_id, "Eltern", "relation", _parents_config(target=db_id)).json()
    resp = _patch(http_client, db_id, parents["id"], config={**parents["config"], "hasTimeline": True})
    assert resp.status_code == 200, resp.text
    resp = _create(http_client, db_id, "Geschwister", "relation", _self_config(db_id))
    assert resp.status_code == 201, resp.text
    assert _schemas(http_client, db_id)["Geschwister"]["config"]["hasTimeline"] is True


def test_a_family_tree_view_completes_the_set_in_the_callers_language(http_client):
    db_id = _database(http_client)
    resp = _family_tree(http_client, db_id, {"Accept-Language": "de"})
    assert resp.status_code == 201, resp.text

    schemas = _schemas(http_client, db_id)
    roles = {s["config"].get(kinship.ROLE_KEY): name for name, s in schemas.items() if s["config"]}
    assert roles == {
        kinship.PARENTS: "Eltern", kinship.CHILDREN: "Kinder", kinship.PARTNERS: "Partner",
        kinship.SIBLINGS: "Geschwister",
        kinship.BIRTH: "Geburtsdatum", kinship.DEATH: "Todeszeitpunkt",
        kinship.SEX: "Geschlecht (Biologisch)",
    }
    for name in ("Eltern", "Kinder", "Partner", "Geschwister"):
        config = schemas[name]["config"]
        assert config["hasTimeline"] is True
        assert config["nuance"]["enabled"] is True
    sex = schemas["Geschlecht (Biologisch)"]["config"]
    assert [o["label"] for o in sex["options"]] == ["weiblich", "männlich"]
    assert sex[kinship.FEMALE_OPTION_KEY] == sex["options"][0]["id"]
    assert sex[kinship.MALE_OPTION_KEY] == sex["options"][1]["id"]


def test_completion_keeps_existing_roles_and_wraps_values_into_timelines(http_client):
    db_id = _database(http_client)
    _create(http_client, db_id, "Parents", "relation", _parents_config("Children", db_id))
    schemas = _schemas(http_client, db_id)
    entry_a = http_client.post(f"/api/databases/{db_id}/entries").json()["id"]
    entry_b = http_client.post(f"/api/databases/{db_id}/entries").json()["id"]
    resp = http_client.put(
        f"/api/databases/{db_id}/entries/{entry_a}/values/{schemas['Parents']['id']}",
        json={"value": {"related_ids": [entry_b]}},
    )
    assert resp.status_code == 200, resp.text

    assert _family_tree(http_client, db_id).status_code == 201
    schemas = _schemas(http_client, db_id)
    assert "Eltern" not in schemas and "Kinder" not in schemas
    assert schemas["Children"]["config"]["hasTimeline"] is True

    with db_module.SessionLocal() as db:
        stored = db.scalar(select(PropertyValue.value).where(
            PropertyValue.page_id == uuid.UUID(entry_a),
            PropertyValue.property_schema_id == uuid.UUID(schemas["Parents"]["id"]),
        ))
    assert "_timeline" in stored


def test_completion_refuses_a_reserved_name_taken_by_another_structure(http_client):
    db_id = _database(http_client)
    with_name = _create(http_client, db_id, "Other", "text").json()
    # A legacy property may carry a reserved name without the structure.
    with db_module.SessionLocal() as db:
        schema = repo.get_schema(db, uuid.UUID(with_name["id"]))
        schema.name = "Partners"
        db.commit()
    resp = _family_tree(http_client, db_id)
    assert resp.status_code == 409


def test_a_family_tree_locks_deleting_and_switching_off_its_edges(http_client):
    db_id = _database(http_client)
    assert _family_tree(http_client, db_id).status_code == 201
    schemas = _schemas(http_client, db_id)
    parents = schemas["Parents"]

    resp = http_client.delete(f"/api/databases/{db_id}/schemas/{parents['id']}")
    assert resp.status_code == 422
    off = {**parents["config"], "hasTimeline": False}
    assert _patch(http_client, db_id, parents["id"], config=off).status_code == 422
    nuance_off = {**parents["config"], "nuance": {**parents["config"]["nuance"], "enabled": False}}
    assert _patch(http_client, db_id, parents["id"], config=nuance_off).status_code == 422
    # Affixes stay editable; the options the client sends back are dropped.
    framed = {**parents["config"], "nuance": {**parents["config"]["nuance"], "affix1": "Prefix A"}}
    resp = _patch(http_client, db_id, parents["id"], config=framed)
    assert resp.status_code == 200, resp.text
    assert resp.json()["config"]["nuance"]["affix1"] == "Prefix A"

    siblings = schemas["Siblings"]
    off = {**siblings["config"], "hasTimeline": False}
    assert _patch(http_client, db_id, siblings["id"], config=off).status_code == 422
    partners = schemas["Partners"]
    off = {**partners["config"], "hasTimeline": False}
    assert _patch(http_client, db_id, partners["id"], config=off).status_code == 422
