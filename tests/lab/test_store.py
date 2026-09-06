from __future__ import annotations

import sqlite3

import pytest

from constella_lab.store import BindingConflictError, LabStore, LastAdminError


def create_user(
    store: LabStore, *, subject: str, email: str, bootstrap: str = "admin@example.com"
) -> dict:
    return store.resolve_identity(
        issuer="https://test.cloudflareaccess.com",
        subject=subject,
        email=email,
        bootstrap_admin_email=bootstrap,
        request_id="request-1",
    )


def test_same_email_new_subject_requires_identity_review(tmp_path) -> None:
    store = LabStore(tmp_path / "identity.sqlite3")
    store.open()
    try:
        create_user(store, subject="original", email="member@example.com")
        duplicate = create_user(store, subject="replacement", email="MEMBER@example.com")
    finally:
        store.close()

    assert duplicate["status"] == "pending_identity_review"


def test_active_binding_uniqueness_rolls_back_entire_batch(tmp_path) -> None:
    store = LabStore(tmp_path / "identity.sqlite3")
    store.open()
    try:
        first = create_user(store, subject="first", email="first@example.com")
        second = create_user(store, subject="second", email="second@example.com")
        assert first["onboarding_completed_at"] is None
        store.create_bindings(
            user_id=first["id"],
            actor_user_id=first["id"],
            request_id="request-1",
            accounts=[
                {
                    "node_id": "node-a",
                    "canonical_username": "first",
                    "uid": 1001,
                    "gid": 1001,
                }
            ],
        )
        with pytest.raises(BindingConflictError):
            store.create_bindings(
                user_id=second["id"],
                actor_user_id=second["id"],
                request_id="request-2",
                accounts=[
                    {
                        "node_id": "node-b",
                        "canonical_username": "second",
                        "uid": 2001,
                        "gid": 2001,
                    },
                    {
                        "node_id": "node-a",
                        "canonical_username": "stolen",
                        "uid": 1001,
                        "gid": 1001,
                    },
                ],
            )
        assert store.user_with_bindings(first["id"])["onboarding_completed_at"] is not None
        assert store.user_with_bindings(second["id"])["onboarding_completed_at"] is None
        assert store.list_bindings(user_id=second["id"]) == []
    finally:
        store.close()


def test_sqlite_backup_restores_identity_state(tmp_path) -> None:
    source = LabStore(tmp_path / "identity.sqlite3")
    source.open()
    try:
        create_user(source, subject="admin", email="admin@example.com")
        backup_path = tmp_path / "backups" / "identity.sqlite3"
        source.backup(backup_path)
    finally:
        source.close()

    restored = LabStore(backup_path)
    restored.open()
    try:
        assert len(restored.list_users()) == 1
        assert restored.list_users()[0]["role"] == "admin"
    finally:
        restored.close()


def test_existing_binding_migrates_to_completed_onboarding(tmp_path) -> None:
    path = tmp_path / "identity.sqlite3"
    store = LabStore(path)
    store.open()
    try:
        member = create_user(store, subject="member", email="member@example.com")
        store.create_bindings(
            user_id=member["id"],
            actor_user_id=member["id"],
            request_id="request-2",
            accounts=[
                {
                    "node_id": "node-a",
                    "canonical_username": "member",
                    "uid": 1001,
                    "gid": 1001,
                }
            ],
        )
    finally:
        store.close()

    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE lab_users DROP COLUMN onboarding_completed_at")
        connection.execute("DELETE FROM lab_schema_migrations WHERE version = 2")

    migrated = LabStore(path)
    migrated.open()
    try:
        user = migrated.user_with_bindings(member["id"])
        assert user is not None
        assert user["onboarding_completed_at"] is not None
    finally:
        migrated.close()


def test_last_active_admin_cannot_be_disabled(tmp_path) -> None:
    store = LabStore(tmp_path / "identity.sqlite3")
    store.open()
    try:
        admin = create_user(store, subject="admin", email="admin@example.com")
        with pytest.raises(LastAdminError):
            store.admin_update_user(
                admin["id"],
                actor_user_id=admin["id"],
                request_id="request-2",
                status="disabled",
            )
        assert store.user_with_bindings(admin["id"])["status"] == "active"
    finally:
        store.close()


def test_reviewed_identity_migration_preserves_user_and_bindings(tmp_path) -> None:
    store = LabStore(tmp_path / "identity.sqlite3")
    store.open()
    try:
        admin = create_user(store, subject="admin", email="admin@example.com")
        member = create_user(store, subject="old-subject", email="member@example.com")
        store.create_bindings(
            user_id=member["id"],
            actor_user_id=member["id"],
            request_id="request-2",
            accounts=[
                {
                    "node_id": "node-a",
                    "canonical_username": "member",
                    "uid": 1001,
                    "gid": 1001,
                }
            ],
        )
        pending = create_user(
            store,
            subject="new-subject",
            email="MEMBER@example.com",
        )

        migrated = store.migrate_identity(
            member["id"],
            pending_user_id=pending["id"],
            actor_user_id=admin["id"],
            request_id="request-3",
            reason="Access identity was recreated",
        )

        assert migrated["id"] == member["id"]
        assert migrated["access_subject"] == "new-subject"
        assert len(migrated["bindings"]) == 1
        assert store.user_with_bindings(pending["id"])["status"] == "disabled"
    finally:
        store.close()
