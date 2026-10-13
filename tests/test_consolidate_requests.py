import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("consolidate", Path(__file__).parents[1] / "scripts/consolidate_requests.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize("owner,target,sources", [("invalid", "a"*32, ["b"*32]), ("c"*32, "a"*32, []), ("c"*32, "a"*32, ["a"*32])])
def test_rejects_ambiguous_or_unsafe_manifest(owner, target, sources):
    with pytest.raises(ValueError):
        module.merge_sql(owner, target, sources, "Doors")


def test_merge_is_transactional_preserves_history_and_rejects_active_sends():
    sql = module.merge_sql("c"*32, "a"*32, ["b"*32], "Owner's doors")
    assert "Owner''s doors" in sql
    assert "DELETE " not in sql and "INSERT " not in sql
    assert "delivery_status IN ('queued','sending')" in sql
    assert "different owner" in sql and "legacy request token" in sql
    assert "_merge_manifest" in sql and "FOR UPDATE" in sql
    assert sql.startswith("BEGIN;") and sql.endswith("COMMIT;\n")


def test_rollback_refuses_new_activity_or_active_delivery():
    sql = module.rollback_sql("a"*32)
    assert "new campaign activity" in sql
    assert "outbound delivery still active" in sql


def test_recipient_account_is_preserved_and_missing_consent_does_not_fallback():
    from farq.haraj_broker import recipient_consent
    old = {"account": "old", "recipients": ["one"]}
    new = {"account": "new", "recipients": ["two"]}
    attrs = {"_haraj_consent": old, "_merge_recipient_consents": {"one": old, "two": new}}
    assert recipient_consent(attrs, "one") == old
    assert recipient_consent(attrs, "two") == new
    assert recipient_consent(attrs, "unknown") == {}
    assert recipient_consent({"_haraj_consent": old}, "one") == old
