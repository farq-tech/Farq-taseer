from unittest.mock import Mock
import json
import pytest
from farq.store import Store
from farq.haraj_chat import HarajChatUnavailable, InboundMessage, SentMessage
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

def request(s, owner=A, need='25 باب PVC', consent=True):
 local=s._connection.execute('select id from users where farq_user_id=?',(owner,)).fetchone()['id']
 attrs={'_haraj_consent':{'owner':owner,'at':'2026-10-06T19:00:00Z','account':'101','recipients':['haraj:seller:202']}} if consent else {}
 return s.create_request(local,need,need,None,'الرياض',attrs,[RequestRecipient(seller_id='haraj:seller:202',seller_name='synthetic',need=need)],supplier_message='بكم الباب؟')

def test_exact_message_and_owner_come_from_owned_request_not_caller(store):
 rid=request(store)
 item=store.claim_deliveries(limit=1)[0]
 broker=UserHarajBroker({},Mock()); broker.call=Mock(return_value={'status':'ACCEPTED','conversation_id':'p2p101_202','message_id':'p2p101_202:7','seq':7,'account_id':'101'})
 receipt=broker.send_delivery(store,item)
 _,owner,payload=broker.call.call_args.args
 assert owner==A and payload['text']=='بكم الباب؟' and payload['recipientId']=='202'
 assert payload['expectedAccountId']=='101' and payload['supplierId']=='haraj:seller:202'
 assert receipt.account_id=='101'

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
 assert mock.send_delivery.call_args.args[1]['body']=='بكم الباب؟'

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
