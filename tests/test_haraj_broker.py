import hashlib
import hmac
import json
from unittest.mock import Mock
import httpx
import pytest
from farq.haraj_broker import UserHarajBroker
from farq.haraj_chat import HarajChatUnavailable

OWNER='11111111-1111-4111-8111-111111111111'
ENV={'HARAJ_CONNECTION_SERVICE_URL':'https://farq.example.test/api/haraj/connection/service','HARAJ_CONNECTION_SERVICE_SECRET':'synthetic-secret-with-more-than-32-characters'}

def test_signature_binds_verified_owner_and_exact_arabic_buyer_text():
    http=Mock()
    http.post.return_value=httpx.Response(200,json={'data':{'status':'CONNECTED'}})
    broker=UserHarajBroker(ENV,http)
    assert broker.call('verify',OWNER,{'text':'بكم الباب؟'}) == {'status':'CONNECTED'}
    kwargs=http.post.call_args.kwargs
    body=kwargs['content'].decode()
    assert json.loads(body)['owner']==OWNER
    headers=kwargs['headers']
    expected=hmac.new(ENV['HARAJ_CONNECTION_SERVICE_SECRET'].encode(),(headers['X-Haraj-Timestamp']+'\n'+body).encode(),hashlib.sha256).hexdigest()
    assert headers['X-Haraj-Signature']==expected
    assert 'Authorization' not in headers and 'Cookie' not in headers

def test_bad_owner_or_unconfigured_bridge_never_calls_network():
    http=Mock()
    with pytest.raises(HarajChatUnavailable,match='HARAJ_USER_CONNECTION_REQUIRED'):
        UserHarajBroker(ENV,http).call('verify','invalid')
    with pytest.raises(HarajChatUnavailable,match='HARAJ_CONFIGURATION_REQUIRED'):
        UserHarajBroker({},http).call('verify',OWNER)
    assert not http.mock_calls

def test_unknown_send_response_is_not_retried_or_logged():
    http=Mock();http.post.side_effect=RuntimeError('synthetic-sensitive-upstream')
    with pytest.raises(HarajChatUnavailable,match='HARAJ_SEND_UNCERTAIN'):
        UserHarajBroker(ENV,http).call('send',OWNER,{'text':'synthetic'})
    assert http.post.call_count==1


def test_direct_send_is_request_scoped_and_records_acceptance(monkeypatch):
    from farq.haraj_broker import send_request_directly
    store=Mock()
    store.get_request.return_value=object()
    store.claim_deliveries.side_effect=[[{'id':'one','request_id':'approved'}],[]]
    broker=Mock(); receipt=object();broker.send_delivery.return_value=receipt
    monkeypatch.setattr('farq.haraj_broker.UserHarajBroker',lambda:broker)
    assert send_request_directly(store,'approved','local-owner')==1
    store.get_request.assert_called_once_with('approved','local-owner')
    assert all(c.kwargs=={'limit':1,'request_id':'approved'} for c in store.claim_deliveries.call_args_list)
    store.finish_delivery.assert_called_once_with('one',sent=receipt)


def test_direct_send_cannot_claim_another_owners_request(monkeypatch):
    from farq.haraj_broker import send_request_directly
    store=Mock();store.get_request.return_value=None
    with pytest.raises(HarajChatUnavailable,match='HARAJ_CONVERSATION_OWNERSHIP'):
        send_request_directly(store,'someone-elses-request','owner-a')
    store.claim_deliveries.assert_not_called()


@pytest.mark.parametrize('code',['HARAJ_SEND_UNCERTAIN','HARAJ_RATE_LIMITED','HARAJ_CHALLENGE_REQUIRED','HARAJ_REAUTH_REQUIRED'])
def test_direct_send_stops_remaining_recipients_without_retry(monkeypatch,code):
    from farq.haraj_broker import send_request_directly
    store=Mock();store.get_request.return_value=object()
    store.claim_deliveries.side_effect=[[{'id':'one'}],[{'id':'two'}],[]]
    broker=Mock();broker.send_delivery.side_effect=HarajChatUnavailable(code)
    monkeypatch.setattr('farq.haraj_broker.UserHarajBroker',lambda:broker)
    assert send_request_directly(store,'approved','local-owner')==0
    assert broker.send_delivery.call_count==1
    assert store.finish_delivery.call_args_list[0].kwargs=={'error':code,'retry':False}
    assert store.finish_delivery.call_args_list[1].kwargs=={'error':'HARAJ_ACTION_STOPPED','retry':False}


def test_real_store_claim_skips_older_requests_and_other_users(tmp_path):
    from farq.store import Store
    from farq.contracts import RequestRecipient
    store=Store(tmp_path/'scoped.sqlite3',tmp_path/'uploads')
    owner_a=store.register('a@example.test','synthetic')
    owner_b=store.register('b@example.test','synthetic')
    recipient=[RequestRecipient(seller_id='900000002',seller_name='synthetic')]
    older=store.create_request(owner_b,'older','door',None,'الرياض',{},recipient,supplier_message='بكم؟')
    current=store.create_request(owner_a,'current','door',None,'الرياض',{},recipient,supplier_message='بكم؟')
    claimed=store.claim_deliveries(request_id=current)
    assert len(claimed)==1 and claimed[0]['request_id']==current
    assert store.claim_deliveries(request_id=current)==[]
    assert store._connection.execute('select delivery_status,attempts from message_deliveries where request_id=?',(older,)).fetchone()['delivery_status']=='queued'
