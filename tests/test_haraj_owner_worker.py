from unittest.mock import Mock
import json
from uuid import UUID
import pytest
from farq.store import Store
from farq.haraj_chat import HarajChatUnavailable, InboundMessage, SentMessage, find_references, with_reference
from farq.contracts import RequestRecipient
from farq.haraj_broker import UserHarajBroker, send_request_directly, sync_user_replies, owned_unmatched

A='11111111-1111-4111-8111-111111111111'
B='22222222-2222-4222-8222-222222222222'

@pytest.fixture
def store(tmp_path):
 s=Store(tmp_path/'owner.sqlite3',tmp_path/'uploads')
 s.login_farq(A,'a@example.test','A',True)
 s.login_farq(B,'b@example.test','B',True)
 return s

def request(s, owner=A, need='25 باب PVC', consent=True, seller='haraj:seller:202'):
 local=s._connection.execute('select id from users where farq_user_id=?',(owner,)).fetchone()['id']
 attrs={'_haraj_consent':{'owner':owner,'at':'2026-10-06T19:00:00Z','account':'101','recipients':[seller]}} if consent else {}
 return s.create_request(local,need,need,None,'الرياض',attrs,[RequestRecipient(seller_id=seller,seller_name='synthetic',need=need)],supplier_message='بكم الباب؟')

def test_with_reference_uses_parsed_codes_not_substrings():
 body='استفسار عن الطلب T-1234567'
 sent=with_reference(body,'T-123456')
 assert sent.endswith('فضلاً اذكر رقم الطلب عند الرد: T-123456')
 assert find_references(sent) == ['T-123456']
 assert with_reference('سؤالي T-123456','T-123456')=='سؤالي T-123456'

def test_exact_message_and_owner_come_from_owned_request_not_caller(store):
 rid=request(store)
 item=store.claim_deliveries(limit=1)[0]
 broker=UserHarajBroker({},Mock()); broker.call=Mock(return_value={'status':'ACCEPTED','conversation_id':'p2p101_202','message_id':'p2p101_202:7','seq':7,'account_id':'101'})
 receipt=broker.send_delivery(store,item)
 _,owner,payload=broker.call.call_args.args
 assert owner==A and payload['text'].startswith('بكم الباب؟\n') and payload['text'].endswith(f"فضلاً اذكر رقم الطلب عند الرد: {item['ref_code']}") and payload['recipientId']=='202'
 assert payload['rfqId']==str(UUID(rid)) and payload['deliveryId']==item['id']
 assert payload['itemId']=='25 باب PVC'
 assert payload['expectedAccountId']=='101' and payload['supplierId']=='haraj:seller:202'
 assert receipt.account_id=='101'

def test_send_refuses_delivery_without_request_reference(store):
 request(store)
 item=store.claim_deliveries(limit=1)[0]
 item['ref_code']=None
 broker=UserHarajBroker({},Mock()); broker.call=Mock()
 with pytest.raises(HarajChatUnavailable,match='HARAJ_REQUEST_REFERENCE_REQUIRED'):
  broker.send_delivery(store,item)
 broker.call.assert_not_called()

def test_old_central_queue_cannot_be_sent_using_customer_session(store):
 request(store,consent=False)
 item=store.claim_deliveries(limit=1)[0]
 broker=UserHarajBroker({},Mock());broker.call=Mock()
 with pytest.raises(HarajChatUnavailable,match='HARAJ_CONSENT_REQUIRED'):
  broker.send_delivery(store,item)
 broker.call.assert_not_called()

def test_dispatch_acceptance_preserves_provider_receipt_without_company_footer(store,monkeypatch):
 rid=request(store)
 mock=Mock(configured=True)
 mock.send_delivery.return_value=SentMessage('p2p101_202','p2p101_202:7',7,'101')
 monkeypatch.setattr('farq.haraj_broker.UserHarajBroker',lambda:mock)
 assert send_request_directly(store,rid,store.request_owner(rid))==1
 delivery=store._connection.execute('select * from message_deliveries').fetchone()
 assert delivery['haraj_account_id']=='101' and delivery['haraj_message_id']=='p2p101_202:7'
 item=mock.send_delivery.call_args.args[1]
 assert item['body']=='بكم الباب؟' and item['ref_code']==store.get_request(rid,store.request_owner(rid)).ref_code

def test_inbound_owner_and_quote_routing_and_per_user_read(store,monkeypatch):
 rid=request(store)
 item=store.claim_deliveries(limit=1)[0]
 store.finish_delivery(item['id'],sent=SentMessage('p2p101_202','p2p101_202:7',7,'101'))
 mock=Mock(configured=True)
 mock.read_thread.return_value=[InboundMessage('p2p101_202:8','متوفر، الحبة 780 ريال','2026-10-06T19:30:00Z',8)]
 monkeypatch.setattr('farq.haraj_broker.UserHarajBroker',lambda:mock)
 assert sync_user_replies(store,10,lambda:2000000000)==1
 localA=store.request_owner(rid);localB=store._connection.execute('select id from users where farq_user_id=?',(B,)).fetchone()['id']
 assert store.get_request(rid,localB) is None
 record=store.get_request(rid,localA)
 assert any(m.body=='متوفر، الحبة 780 ريال' for m in record.messages)
 assert record.offers
 assert record.offers[0].quantity==25 and record.offers[0].unit_price==780
 assert record.offers[0].amount==19500 and record.offers[0].availability=='available'
 store.mark_read(rid,localA)
 assert store.get_request(rid,localB) is None

def test_ambiguous_reply_stays_visible_to_its_owner_only_and_is_not_guessed(store,monkeypatch):
 ids=[request(store,need=n) for n in ['25 باب PVC','10 شبابيك']]
 for item in store.claim_deliveries(limit=2):
  store.finish_delivery(item['id'],sent=SentMessage('p2p101_202',f"p2p101_202:{7+ids.index(item['request_id'])}",7+ids.index(item['request_id']),'101'))
 mock=Mock(configured=True);mock.read_thread.return_value=[InboundMessage('p2p101_202:9','780 ريال','2026-10-06T19:30:00Z',9)]
 monkeypatch.setattr('farq.haraj_broker.UserHarajBroker',lambda:mock)
 assert sync_user_replies(store,10,lambda:2000000000)==0
 localA=store.request_owner(ids[0]);localB=store._connection.execute('select id from users where farq_user_id=?',(B,)).fetchone()['id']
 assert len(owned_unmatched(store,localA))==1
 assert owned_unmatched(store,localB)==[]
 assert all(not store.get_request(rid,localA).offers for rid in ids)

def test_reference_routes_reply_to_the_matching_request_in_a_shared_conversation(store,monkeypatch):
 ids=[request(store,need=n) for n in ['25 باب PVC','10 شبابيك']]
 refs={row['id']:row['ref_code'] for row in store._connection.execute(
  'select id, ref_code from requests where id in (?,?)',ids
 )}
 for index,item in enumerate(store.claim_deliveries(limit=2),start=7):
  store.finish_delivery(item['id'],sent=SentMessage('p2p101_202',f'p2p101_202:{index}',index,'101'))
 target=ids[0]
 reply=InboundMessage('p2p101_202:9',f'بالنسبة لطلبك {refs[target]} متوفر بـ ٧٠٠ ريال','2026-10-06T19:30:00Z',9)
 mock=Mock(configured=True);mock.read_thread.return_value=[reply]
 monkeypatch.setattr('farq.haraj_broker.UserHarajBroker',lambda:mock)

 assert sync_user_replies(store,10,lambda:2000000000)==1
 local=store.request_owner(target)
 target_record=store.get_request(target,local)
 other_id=next(rid for rid in ids if rid!=target)
 other_record=store.get_request(other_id,local)
 assert any(message.body==reply.body for message in target_record.messages)
 assert target_record.offers and target_record.offers[0].amount==700
 assert not any(message.body==reply.body for message in other_record.messages)
 assert not other_record.offers
 assert owned_unmatched(store,local)==[]

def test_reference_from_merged_source_request_routes_to_consolidated_target(store,monkeypatch):
 source=request(store,need='source item',seller='haraj:seller:202')
 target=request(store,need='combined request',seller='haraj:seller:303')
 source_ref=store._connection.execute('select ref_code from requests where id=?',(source,)).fetchone()['ref_code']
 for item in store.claim_deliveries(limit=2):
  seller=item['seller_id'].split(':')[-1]
  store.finish_delivery(item['id'],sent=SentMessage(f'p2p101_{seller}',f'p2p101_{seller}:7',7,'101'))

 source_row=store._connection.execute('select attributes_json from requests where id=?',(source,)).fetchone()
 attrs=json.loads(source_row['attributes_json'])
 attrs['merged_into_request_id']=target
 store._connection.execute('update requests set attributes_json=? where id=?',(json.dumps(attrs),source))
 target_row=store._connection.execute('select attributes_json from requests where id=?',(target,)).fetchone()
 target_attrs=json.loads(target_row['attributes_json'])
 target_attrs['_merge_recipient_consents']={
  'haraj:seller:202':attrs['_haraj_consent'],
  'haraj:seller:303':target_attrs['_haraj_consent'],
 }
 store._connection.execute('update requests set attributes_json=? where id=?',(json.dumps(target_attrs),target))
 store._connection.execute('update haraj_threads set request_id=? where request_id=?',(target,source))
 store._connection.execute('update request_recipients set request_id=? where request_id=?',(target,source))
 store._connection.execute('update message_deliveries set request_id=? where request_id=?',(target,source))
 store._connection.commit()

 reply=InboundMessage('p2p101_202:8',f'{source_ref} متوفر بـ ٧٠٠ ريال','2026-10-06T19:30:00Z',8)
 rows=store.threads_to_sync()
 source_thread=next(row for row in rows if row['haraj_conversation_id']=='p2p101_202')
 assert source_ref in source_thread['source_ref_codes']
 mock=Mock(configured=True)
 mock.read_thread.side_effect=lambda _store,thread,_after: [reply] if thread['haraj_conversation_id']=='p2p101_202' else []
 monkeypatch.setattr('farq.haraj_broker.UserHarajBroker',lambda:mock)

 assert sync_user_replies(store,10,lambda:2000000000)==1
 local=store.request_owner(target)
 record=store.get_request(target,local)
 assert any(message.body==reply.body for message in record.messages)
 assert record.offers and record.offers[0].amount==700
 assert owned_unmatched(store,local)==[]

def test_multiple_items_in_one_request_route_replies_to_request_without_guessing_quote_line(store,monkeypatch):
 local=store._connection.execute('select id from users where farq_user_id=?',(A,)).fetchone()['id']
 needs=['عربية اطفال يويو اصلية','عربية اطفال yoyo','عربية اطفال يويو الحي: الربيع']
 rid=store.create_request(
  local,'عربية اطفال يويو','عربية اطفال يويو',None,'الرياض',
  {'_haraj_consent':{'owner':A,'at':'2026-10-06T19:00:00Z','account':'101','recipients':['haraj:seller:202']}},
  [RequestRecipient(seller_id='haraj:seller:202',seller_name='synthetic',need=need) for need in needs],
  supplier_message='متوفر عندك؟',
 )
 for index,item in enumerate(store.claim_deliveries(limit=3,request_id=rid),start=7):
  store.finish_delivery(item['id'],sent=SentMessage('p2p101_202',f'p2p101_202:{index}',index,'101'))
 replies=[
  InboundMessage('p2p101_202:10','متوفره اخوي','2026-10-06T19:30:00Z',10),
  InboundMessage('p2p101_202:11','بالرياض','2026-10-06T19:31:00Z',11),
  InboundMessage('p2p101_202:12','٧٠٠','2026-10-06T19:32:00Z',12),
 ]
 mock=Mock(configured=True);mock.read_thread.return_value=replies
 monkeypatch.setattr('farq.haraj_broker.UserHarajBroker',lambda:mock)

 assert sync_user_replies(store,10,lambda:2000000000)==3
 record=store.get_request(rid,local)
 assert [message.body for message in record.messages if message.sender_role=='seller']==[reply.body for reply in replies]
 assert all(message.need is None for message in record.messages if message.sender_role=='seller')
 assert not record.offers
 assert owned_unmatched(store,local)==[]

def test_farq_mapping_cannot_be_reassigned_and_worker_rejects_forged_consent_owner(store):
 rid=request(store)
 local=store.request_owner(rid)
 assert store.link_farq(local,B) is False
 assert store.farq_user_for_request(rid)==A
 with pytest.raises(Exception):
  store._connection.execute('update users set farq_user_id=? where id=?',(B,local))
 item=store.claim_deliveries(limit=1)[0]
 attributes=json.loads(item['consent_attributes'])
 attributes['_haraj_consent']['owner']=B
 item['consent_attributes']=json.dumps(attributes)
 broker=UserHarajBroker({},Mock());broker.call=Mock()
 with pytest.raises(HarajChatUnavailable) as rejected:
  broker.send_delivery(store,item)
 assert rejected.value.code=='HARAJ_CONSENT_REQUIRED'
 broker.call.assert_not_called()


def test_legacy_shared_threads_cannot_read_or_steal_new_account_replies(store, monkeypatch):
 old_a=request(store, consent=False)
 old_b=request(store, owner=B, consent=False)
 current=request(store)
 for rid in [old_a,old_b,current]:
  item=store.claim_deliveries(request_id=rid)[0]
  store.finish_delivery(item['id'],sent=SentMessage('p2p101_202','p2p101_202:7',7,'101'))
 broker=Mock(configured=True)
 broker.read_thread.return_value=[InboundMessage('p2p101_202:8','متوفر، الحبة 780 ريال','2026-10-06T19:30:00Z',8)]
 monkeypatch.setattr('farq.haraj_broker.UserHarajBroker',lambda:broker)
 assert sync_user_replies(store,10,lambda:2000000000)==1
 assert broker.read_thread.call_count==1
 assert broker.read_thread.call_args.args[1]['request_id']==current
 assert store.get_request(current,store.request_owner(current)).offers
 for rid in [old_a,old_b]:
  assert not store.get_request(rid,store.request_owner(rid)).offers
  row=store._connection.execute('select failure_code from haraj_threads where request_id=?',(rid,)).fetchone()
  assert row['failure_code']=='HARAJ_CONSENT_REQUIRED'


@pytest.mark.parametrize('bad', ['corrupt', {'recipients':None}, []])
def test_malformed_legacy_consent_cannot_call_provider(store, monkeypatch, bad):
 rid=request(store)
 item=store.claim_deliveries(request_id=rid)[0]
 store.finish_delivery(item['id'],sent=SentMessage('p2p101_202','p2p101_202:7',7,'101'))
 store._connection.execute('update requests set attributes_json=? where id=?',(json.dumps({'_haraj_consent':bad}),rid))
 store._connection.commit()
 broker=Mock(configured=True)
 monkeypatch.setattr('farq.haraj_broker.UserHarajBroker',lambda:broker)
 assert sync_user_replies(store,10,lambda:2000000000)==0
 broker.read_thread.assert_not_called()
