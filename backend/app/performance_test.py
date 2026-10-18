"""
Performance budgets.

These tests do not fail when a budget is exceeded. They emit a
``PerformanceBudgetWarning``, which pytest lists in its warnings summary, so a
regression is visible on every run without blocking a build on a slow machine.

Two kinds of budget are checked:

* **Scaling.** The number of SQL statements an endpoint issues must not grow
  with the amount of data it returns. Each scenario runs once with a small and
  once with a larger data set and compares the counts. This is deterministic:
  it does not depend on the machine, and it is the check that catches an
  endpoint that has started querying once per row.
* **Time.** A wall-clock ceiling for a few representative requests, generous
  enough that only a gross regression trips it. Timings are only meaningful on
  PostgreSQL, which is what the suite runs against.

Every scenario also asserts the response status, so a broken endpoint still
fails the test rather than producing a misleading count.
"""
import time
import uuid
import warnings
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import app.database.database as db_module
from app.automations import automations_repository as auto_repo
from app.blocks import repository as repo
from app.blocks.models import WORKSPACE_ROOT_ID, Block
from app.main import app
from app.permissions import repository as perm_repo
from app.session.deps import get_current_user
from app.users.model import User


class PerformanceBudgetWarning(UserWarning):
    """A request exceeded its query or time budget."""


# Wall-clock ceilings in seconds. Deliberately loose: they flag an order of
# magnitude, not a few percent.
_TIME_BUDGET_QUERY_200_ENTRIES = 2.0
_TIME_BUDGET_CHILDREN_200 = 1.0
_TIME_BUDGET_VIEW_PAGE_200_ENTRIES = 2.0

# Statements an authenticated request may spend on getting the caller and the
# block before the handler does its own work: session token, user, block,
# permission walk.
_AUTH_REQUEST_QUERY_BUDGET = 8


def _warn(message: str) -> None:
    warnings.warn(PerformanceBudgetWarning(message), stacklevel=2)


def _expect_flat(name: str, small: int, large: int) -> None:
    """Warn when the larger data set needed more statements than the smaller."""
    if large > small:
        _warn(f"{name}: {small} queries for the small data set, {large} for the large one")


@contextmanager
def _timed(name: str, budget: float):
    start = time.perf_counter()
    yield
    elapsed = time.perf_counter() - start
    if elapsed > budget:
        _warn(f"{name}: {elapsed:.2f}s, budget {budget:.2f}s")


# ─── Seeding ─────────────────────────────────────────────────────────────────


def _now():
    return datetime.now(timezone.utc)


def _seed_root(db) -> None:
    db.add(Block(
        id=WORKSPACE_ROOT_ID, type="workspace", position=0.0, state="active",
        content={"title": "Workspace"}, created_at=_now(), updated_at=_now(),
    ))
    db.flush()
    perm_repo.set_permission(db, WORKSPACE_ROOT_ID, "everyone", [])
    db.commit()


def _member(db) -> User:
    user = User(
        id=uuid.uuid4(),
        username=f"member_{uuid.uuid4().hex[:8]}",
        password_hash="x",
        role="member",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.expunge(user)
    return user


def _private_page(db, owner: User) -> uuid.UUID:
    page = repo.create_block(db, type="page", position=1.0, parent_id=WORKSPACE_ROOT_ID)
    page.owner_id = owner.id
    db.flush()
    perm_repo.set_permission(db, page.id, "private", [])
    db.commit()
    return page.id


def _database_with_entries(db, owner: User, count: int) -> uuid.UUID:
    """A private database with a text, a select and a relation column, filled."""
    database = repo.create_block(db, type="database", position=2.0, parent_id=WORKSPACE_ROOT_ID)
    database.owner_id = owner.id
    db.flush()
    perm_repo.set_permission(db, database.id, "private", [])
    text = repo.create_schema(db, database_id=database.id, name="Text", type="text", position=1.0)
    choice = repo.create_schema(
        db, database_id=database.id, name="Choice", type="select", position=2.0,
        config={"options": ["A", "B"]},
    )
    link = repo.create_schema(
        db, database_id=database.id, name="Link", type="relation", position=3.0,
        config={"target_database_id": str(database.id)},
    )
    entries = [
        repo.create_block(
            db, type="page", position=float(i), parent_id=database.id,
            content={"title": f"Entry {i}"},
        )
        for i in range(count)
    ]
    db.flush()
    for i, entry in enumerate(entries):
        repo.upsert_value(db, page_id=entry.id, schema_id=text.id, value={"text": f"Value {i}"})
        repo.upsert_value(db, page_id=entry.id, schema_id=choice.id, value={"option": "A"})
        repo.upsert_value(
            db, page_id=entry.id, schema_id=link.id,
            value={"related_ids": [str(entries[(i + 1) % count].id)]},
        )
    db.commit()
    return database.id


@pytest.fixture
def member_client():
    """
    A TestClient authenticated as a member, so permission checks actually run.

    Yields ``(client, user)``. The account is a database row; requests resolve
    it through the dependency override, which keeps the counts focused on the
    endpoint rather than on token handling (measured separately below).
    """
    with db_module.SessionLocal() as db:
        _seed_root(db)
        user = _member(db)
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        with TestClient(app) as client:
            yield client, user
    finally:
        app.dependency_overrides.clear()


# ─── Scaling ─────────────────────────────────────────────────────────────────


def test_children_listing_does_not_query_per_child(member_client, count_queries):
    client, user = member_client
    pages = {}
    with db_module.SessionLocal() as db:
        for size in (5, 50):
            page_id = _private_page(db, user)
            for i in range(size):
                repo.create_block(db, type="paragraph", position=float(i), parent_id=page_id)
            db.commit()
            pages[size] = page_id

    counts = {}
    for size, page_id in pages.items():
        with count_queries() as counter:
            response = client.get(f"/api/blocks/{page_id}/children")
        assert response.status_code == 200
        assert len(response.json()) == size
        counts[size] = counter.count

    _expect_flat("GET /api/blocks/{id}/children", counts[5], counts[50])


def test_entry_query_does_not_query_per_entry(member_client, count_queries):
    client, user = member_client
    with db_module.SessionLocal() as db:
        small = _database_with_entries(db, user, 5)
        large = _database_with_entries(db, user, 50)

    counts = {}
    for name, database_id in (("small", small), ("large", large)):
        with count_queries() as counter:
            response = client.post(
                f"/api/databases/{database_id}/entries/query",
                json={"filter_groups": [], "sorts": [], "limit": 100},
            )
        assert response.status_code == 200
        counts[name] = counter.count

    _expect_flat("POST /api/databases/{id}/entries/query", counts["small"], counts["large"])


def test_title_resolution_does_not_query_per_id(member_client, count_queries):
    client, user = member_client
    with db_module.SessionLocal() as db:
        database_id = _database_with_entries(db, user, 50)
        ids = [str(e.id) for e in repo.list_children(db, database_id, state="active")]

    counts = {}
    for size in (5, 50):
        with count_queries() as counter:
            response = client.post(
                f"/api/databases/{database_id}/entries/resolve-titles",
                json={"ids": ids[:size]},
            )
        assert response.status_code == 200
        assert len(response.json()) == size
        counts[size] = counter.count

    _expect_flat("POST /api/databases/{id}/entries/resolve-titles", counts[5], counts[50])


def _default_view(client, database_id) -> str:
    response = client.get(f"/api/databases/{database_id}/views")
    assert response.status_code == 200
    return response.json()["views"][0]["id"]


def test_view_page_does_not_query_per_entry(member_client, count_queries):
    """
    A view page carries its entries, their values and every resolved
    relation, so this one request replaces the entry query plus one title
    lookup per relation cell.
    """
    client, user = member_client
    with db_module.SessionLocal() as db:
        small = _database_with_entries(db, user, 5)
        large = _database_with_entries(db, user, 50)

    counts = {}
    for name, database_id in (("small", small), ("large", large)):
        view_id = _default_view(client, database_id)
        with count_queries() as counter:
            response = client.post(
                f"/api/databases/{database_id}/views/{view_id}/entries", json={"limit": 100},
            )
        assert response.status_code == 200
        body = response.json()
        assert len(body["relations"]) == len(body["entries"])
        counts[name] = counter.count

    _expect_flat(
        "POST /api/databases/{id}/views/{view_id}/entries", counts["small"], counts["large"],
    )


def test_automation_listing_does_not_query_per_automation(member_client, count_queries):
    client, user = member_client
    counts = {}
    total = 0
    with db_module.SessionLocal() as db:
        database_id = _database_with_entries(db, user, 1)
    for size in (2, 20):
        with db_module.SessionLocal() as db:
            for i in range(size - total):
                auto_repo.create_automation(
                    db, database_id=database_id, name=f"Automation {i}",
                    trigger={}, actions=[],
                )
            db.commit()
        total = size
        with count_queries() as counter:
            response = client.get("/api/automations")
        assert response.status_code == 200
        assert len(response.json()) == size
        counts[size] = counter.count

    _expect_flat("GET /api/automations", counts[2], counts[20])


# ─── Fixed overhead ──────────────────────────────────────────────────────────


def test_authenticated_request_overhead(count_queries):
    """
    A request with a real session cookie: the token, the account and the
    handler share one database session, so the whole request stays within a
    small fixed number of statements.
    """
    import app.session.session as s

    with db_module.SessionLocal() as db:
        _seed_root(db)
        user = _member(db)
    token = s.create_token(user.id)

    with TestClient(app) as client:
        client.cookies.set("session", token)
        with count_queries() as counter:
            response = client.get(f"/api/blocks/{WORKSPACE_ROOT_ID}")
    assert response.status_code == 200

    if counter.count > _AUTH_REQUEST_QUERY_BUDGET:
        _warn(
            f"GET /api/blocks/{{id}} with session cookie: {counter.count} queries, "
            f"budget {_AUTH_REQUEST_QUERY_BUDGET}"
        )


# ─── Time ────────────────────────────────────────────────────────────────────


def test_entry_query_time(member_client):
    client, user = member_client
    with db_module.SessionLocal() as db:
        database_id = _database_with_entries(db, user, 200)

    with _timed("POST /api/databases/{id}/entries/query, 200 entries", _TIME_BUDGET_QUERY_200_ENTRIES):
        response = client.post(
            f"/api/databases/{database_id}/entries/query",
            json={"filter_groups": [], "sorts": [], "limit": 200},
        )
    assert response.status_code == 200
    assert len(response.json()["entries"]) == 200


def test_children_listing_time(member_client):
    client, user = member_client
    with db_module.SessionLocal() as db:
        page_id = _private_page(db, user)
        for i in range(200):
            repo.create_block(db, type="paragraph", position=float(i), parent_id=page_id)
        db.commit()

    with _timed("GET /api/blocks/{id}/children, 200 children", _TIME_BUDGET_CHILDREN_200):
        response = client.get(f"/api/blocks/{page_id}/children")
    assert response.status_code == 200
    assert len(response.json()) == 200


def test_view_page_time(member_client):
    client, user = member_client
    with db_module.SessionLocal() as db:
        database_id = _database_with_entries(db, user, 200)
    view_id = _default_view(client, database_id)

    with _timed(
        "POST /api/databases/{id}/views/{view_id}/entries, 200 entries",
        _TIME_BUDGET_VIEW_PAGE_200_ENTRIES,
    ):
        response = client.post(
            f"/api/databases/{database_id}/views/{view_id}/entries", json={"limit": 200},
        )
    assert response.status_code == 200
    assert len(response.json()["entries"]) == 200
