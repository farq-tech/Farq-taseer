-- One-off, applied 2026-10-04 on farq-main (mpgbvtaguerncgbzvpwg), schema taseer.
-- The seller reply «هلا» (p2p13935624_17035483:1790950549551, 2026-10-02 14:15:49 UTC) sat in
-- haraj_unmatched: seller 17035483 had three open requests from two buyers and the reply quoted
-- no reference. Under the owner rule of 2026-10-04 (store.choose_thread) it belongs to the latest
-- request we had sent him before he wrote: c349b956a76d4a14808c2d1434921de3 («سباك يصلح تسريب
-- حمام», T-188270, last send 2026-09-23 13:31 UTC). Filed as PgStore.record_inbound would (no price
-- in it, so no offer), with the customer's reply notification. The other unmatched rows were left.
with u as (
  delete from taseer.haraj_unmatched
   where haraj_message_id = 'p2p13935624_17035483:1790950549551'
     and not exists (select 1 from taseer.messages where haraj_message_id = 'p2p13935624_17035483:1790950549551')
  returning haraj_message_id, haraj_conversation_id, seller_id, body, media, sent_at
), m as (
  insert into taseer.messages (id, request_id, sender_role, sender_user_id, seller_id, need, reply_to, scope,
    haraj_conversation_id, haraj_message_id, haraj_text, body, offer_amount, offer_currency, attachment_ids, media, created_at)
  select replace(gen_random_uuid()::text, '-', ''), 'c349b956a76d4a14808c2d1434921de3', 'seller', null, u.seller_id, 'سباك يصلح تسريب حمام', null, 'single_seller',
    u.haraj_conversation_id, u.haraj_message_id, null, u.body, null, null, '[]'::jsonb, u.media, u.sent_at
  from u returning id, created_at
)
insert into taseer.notifications (id, user_id, request_id, kind, created_at)
select replace(gen_random_uuid()::text, '-', ''), r.owner_user_id, r.id, 'seller_reply', m.created_at
from m join taseer.requests r on r.id = 'c349b956a76d4a14808c2d1434921de3';
