"""Generate an owner-scoped, reversible campaign consolidation. Prints SQL; never sends.

Review the generated transaction and deploy retained-link support before applying it.
No rows are deleted. Historical tokens, message IDs, delivery states and item needs stay intact.
"""
import argparse
import re

TABLES = ("attachments", "counter_offers", "customer_notifications", "files", "haraj_threads",
          "message_deliveries", "messages", "notifications", "offers", "request_recipients",
          "supplier_funnel", "supplier_notifications")


def merge_sql(owner, target, sources, title):
    ids = [target, *sources]
    if len(ids) < 2 or len(set(ids)) != len(ids) or not all(re.fullmatch(r"[0-9a-f]{32}", x) for x in [owner, *ids]):
        raise ValueError("explicit distinct request IDs and owner ID are required")
    if not title.strip() or len(title) > 500:
        raise ValueError("title is required and must be at most 500 characters")
    array = "ARRAY[" + ",".join("'" + x + "'" for x in ids) + "]::text[]"
    tables = "ARRAY[" + ",".join("'" + x + "'" for x in TABLES) + "]::text[]"
    title = title.replace("'", "''")
    return f"""BEGIN;
SET LOCAL lock_timeout = '5s';
DO $merge$
DECLARE
 ids text[] := {array};
 target text := '{target}';
 owner_id text := '{owner}';
 allowed_tables text[] := {tables};
 tbl text; manifest jsonb := '{{}}'; snapshot jsonb; old_target jsonb;
BEGIN
 PERFORM id FROM taseer.requests WHERE id = ANY(ids) ORDER BY id FOR UPDATE;
 IF (SELECT count(*) FROM taseer.requests WHERE id=ANY(ids) AND owner_user_id=owner_id) <> cardinality(ids)
 THEN RAISE EXCEPTION 'missing request or different owner'; END IF;
 IF EXISTS(SELECT 1 FROM taseer.requests WHERE id=ANY(ids) AND
   (awarded_seller_id IS NOT NULL OR attributes ? 'merged_into_request_id' OR attributes ? '_merge_manifest'))
 THEN RAISE EXCEPTION 'awarded or already consolidated request'; END IF;
 IF EXISTS(SELECT 1 FROM taseer.message_deliveries WHERE request_id=ANY(ids) AND delivery_status IN ('queued','sending'))
 THEN RAISE EXCEPTION 'outbound delivery still active'; END IF;
 IF EXISTS(SELECT seller_id FROM taseer.request_recipients WHERE request_id=ANY(ids) GROUP BY seller_id HAVING count(*)>1)
 THEN RAISE EXCEPTION 'duplicate supplier needs explicit reconciliation'; END IF;
 IF EXISTS(SELECT 1 FROM taseer.requests r WHERE r.id=ANY(ids) AND NOT EXISTS
   (SELECT 1 FROM taseer.request_recipients p WHERE p.request_id=r.id AND p.reply_token=r.reply_token))
 THEN RAISE EXCEPTION 'legacy request token lacks recipient mapping'; END IF;
 IF EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='taseer' AND column_name='request_id'
   AND NOT(table_name=ANY(allowed_tables)))
 THEN RAISE EXCEPTION 'unreviewed request-reference table'; END IF;
 SELECT jsonb_build_object('need',need,'attributes',attributes) INTO old_target FROM taseer.requests WHERE id=target;
 FOREACH tbl IN ARRAY allowed_tables LOOP
   IF tbl='haraj_threads' THEN
     EXECUTE format('SELECT coalesce(jsonb_agg(jsonb_build_object(''request_id'',request_id,''seller_id'',seller_id,''need'',need)),''[]''::jsonb) FROM taseer.%I WHERE request_id=ANY($1) AND request_id<>$2',tbl)
       INTO snapshot USING ids,target;
   ELSE
     EXECUTE format('SELECT coalesce(jsonb_agg(jsonb_build_object(''request_id'',request_id,''id'',id)),''[]''::jsonb) FROM taseer.%I WHERE request_id=ANY($1) AND request_id<>$2',tbl)
       INTO snapshot USING ids,target;
   END IF;
   manifest := manifest || jsonb_build_object(tbl,snapshot);
   EXECUTE format('UPDATE taseer.%I SET request_id=$1 WHERE request_id=ANY($2) AND request_id<>$1',tbl) USING target,ids;
 END LOOP;
 UPDATE taseer.requests SET attributes=attributes || jsonb_build_object('merged_into_request_id',target)
   WHERE id=ANY(ids) AND id<>target;
 UPDATE taseer.requests SET need='{title}', attributes=attributes || jsonb_build_object(
   '_merge_manifest',manifest,'_merge_previous_target',old_target,'_merge_sources',to_jsonb(ids),'_merged_at',now()) WHERE id=target;
END $merge$;
COMMIT;
"""


def rollback_sql(target):
    if not re.fullmatch(r"[0-9a-f]{32}", target):
        raise ValueError("invalid target")
    return f"""BEGIN;
SET LOCAL lock_timeout = '5s';
DO $undo$
DECLARE target text := '{target}'; attrs jsonb; tbl text; snapshot jsonb;
BEGIN
 SELECT attributes INTO attrs FROM taseer.requests WHERE id=target FOR UPDATE;
 IF NOT(attrs ? '_merge_manifest') THEN RAISE EXCEPTION 'no consolidation manifest'; END IF;
 IF EXISTS(SELECT 1 FROM taseer.message_deliveries WHERE request_id=target AND delivery_status IN ('queued','sending'))
 THEN RAISE EXCEPTION 'outbound delivery still active'; END IF;
 IF EXISTS(SELECT 1 FROM taseer.messages WHERE request_id=target AND created_at>(attrs->>'_merged_at')::timestamptz)
 THEN RAISE EXCEPTION 'new campaign activity requires explicit rollback reconciliation'; END IF;
 FOR tbl,snapshot IN SELECT * FROM jsonb_each(attrs->'_merge_manifest') LOOP
   IF NOT(tbl=ANY(ARRAY[{','.join(repr(x) for x in TABLES)}])) THEN RAISE EXCEPTION 'unexpected manifest table'; END IF;
   IF tbl='haraj_threads' THEN
     EXECUTE format('UPDATE taseer.%I d SET request_id=x.request_id FROM jsonb_to_recordset($1) AS x(request_id text,seller_id text,need text) WHERE d.request_id=$2 AND d.seller_id=x.seller_id AND d.need=x.need',tbl) USING snapshot,target;
   ELSE
     EXECUTE format('UPDATE taseer.%I d SET request_id=x.request_id FROM jsonb_to_recordset($1) AS x(request_id text,id text) WHERE d.request_id=$2 AND d.id::text=x.id',tbl) USING snapshot,target;
   END IF;
 END LOOP;
 UPDATE taseer.requests SET attributes=attributes-'merged_into_request_id' WHERE attributes->>'merged_into_request_id'=target;
 UPDATE taseer.requests SET need=attrs->'_merge_previous_target'->>'need',attributes=attrs->'_merge_previous_target'->'attributes' WHERE id=target;
END $undo$;
COMMIT;
"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner")
    parser.add_argument("--target", required=True)
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument("--title")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    print(rollback_sql(args.target) if args.rollback else merge_sql(args.owner or "", args.target, args.source, args.title or ""))
