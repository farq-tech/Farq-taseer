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


def test_read_binds_the_stored_account_and_refuses_unknown_legacy_account():
    store = Mock(); store.farq_user_for_request.return_value = OWNER
    broker = UserHarajBroker(ENV, Mock())
    broker.call = Mock(return_value={'account_id':'101','messages':[]})
    thread = {'request_id':'synthetic','seller_id':'haraj:seller:202','haraj_conversation_id':'p2p101_202','haraj_account_id':'101'}
    assert broker.read_thread(store, thread, 7) == []
    assert broker.call.call_args.args[2]['expectedAccountId'] == '101'
    broker.call.reset_mock()
    with pytest.raises(HarajChatUnavailable, match='HARAJ_CONVERSATION_OWNERSHIP'):
        broker.read_thread(store, {**thread, 'haraj_account_id':None}, 7)
    broker.call.assert_not_called()


def test_uncertain_provider_result_is_visible_without_requeue(tmp_path):
    from farq.store import Store
    from farq.contracts import RequestRecipient
    store = Store(tmp_path/'uncertain.sqlite3', tmp_path/'uploads')
    owner = store.register('test@example.test', 'synthetic')
    request = store.create_request(owner, 'باب', 'باب', None, 'الرياض', {}, [RequestRecipient(seller_id='seller-test',seller_name='تجريبي')],supplier_message='بكم الباب؟')
    delivery = store.claim_deliveries(request_id=request)[0]
    store.finish_delivery(delivery['id'], error='HARAJ_SEND_UNCERTAIN', retry=False)
    record = store.get_request(request, owner)
    assert record.messages[0].delivery_state == 'unknown'
    assert record.messages[0].deliveries[0]['status'] == 'unknown'
    assert not store.claim_deliveries(request_id=request)
    assert store.get_request(request, 'another-owner') is None


def test_shared_routing_bounds_actual_targets_including_attachment_routes():
    from farq.store import route_targets
    recipients=[{"seller_id":str(i),"need":"باب" if i < 40 else "سباك"} for i in range(100)]
    assert len(route_targets(recipients,None,"باب",None)[0]) == 40
    assert len(route_targets(recipients,None,None,"99")[0]) == 1
    with pytest.raises(ValueError,match="٥٠"):
        route_targets(recipients,None,"سباك",None)


def test_explicit_seller_survives_stale_need_after_request_consolidation():
    from farq.store import route_targets

    recipients = [
        {"seller_id": "seller-a", "need": "أبواب WPC"},
        {"seller_id": "seller-b", "need": "أبواب PVC"},
    ]

    targets, need, scope = route_targets(
        recipients,
        "أبواب PVC وWPC — الرياض",
        "أبواب PVC وWPC — الرياض",
        "seller-a",
    )
    assert targets == [recipients[0]]
    assert need == "أبواب WPC"
    assert scope == "single_seller"

    targets, need, scope = route_targets(
        recipients,
        "أبواب PVC وWPC — الرياض",
        "أبواب PVC وWPC — الرياض",
        None,
        ["seller-a"],
    )
    assert targets == [recipients[0]]
    assert need == "أبواب WPC"
    assert scope == "single_seller"


def test_direct_send_spaces_provider_attempts_after_acceptance(monkeypatch):
    from farq.haraj_broker import send_request_directly
    store = Mock(); store.get_request.return_value = object()
    store.claim_deliveries.side_effect = [[{'id':'one'}], [{'id':'two'}], []]
    now = [100.0]; attempts = []; waits = []
    def sleep(seconds):
        waits.append(seconds); now[0] += seconds
    def send(*args):
        attempts.append(now[0]); now[0] += 2.0
        return object()
    broker = Mock(); broker.send_delivery.side_effect = send
    monkeypatch.setattr('farq.haraj_broker.UserHarajBroker', lambda: broker)
    assert send_request_directly(store, 'approved', 'owner', sleep=sleep, clock=lambda:now[0]) == 2
    assert waits == [0.0, 30.0]
    assert attempts == [100.0, 132.0]
    assert store.finish_delivery.call_count == 2


def _thread(**extra):
    return {'request_id':'request-a','seller_id':'haraj:seller:202','need':'باب','haraj_conversation_id':'p2p101_202',
            'haraj_account_id':'101','high_water':3,
            'consent_attributes':{'_haraj_consent':{'owner':OWNER,'at':'2026-10-01T00:00:00Z','account':'101','recipients':['haraj:seller:202']}},
            **extra}


def _sync_with_service_response(monkeypatch, response):
    """Run one reply-sync pass where Farq's service answers every messages read with ``response``."""
    from farq import haraj_broker
    http = Mock(); http.post.return_value = response
    monkeypatch.setattr(haraj_broker, 'UserHarajBroker', lambda: UserHarajBroker(ENV, http))
    store = Mock(); store.farq_user_for_request.return_value = OWNER
    store.threads_to_sync.return_value = [_thread()]
    assert haraj_broker.sync_user_replies(store, 40, lambda: 1000.0) == 0
    return store, http


def test_ownership_refusal_parks_the_conversation_instead_of_retrying(monkeypatch, caplog):
    from farq.haraj_broker import DETACHED_CODE, DETACHED_RECHECK_SECONDS
    refused = httpx.Response(403, json={'errors':[{'code':'HARAJ_CONVERSATION_OWNERSHIP'}]})
    with caplog.at_level('WARNING', logger='farq.haraj_broker'):
        store, http = _sync_with_service_response(monkeypatch, refused)
    assert http.post.call_count == 1
    store.thread_checked.assert_called_once()
    kwargs = store.thread_checked.call_args.kwargs
    assert kwargs['failure_code'] == DETACHED_CODE
    assert kwargs['retry_seconds'] == DETACHED_RECHECK_SECONDS >= 24 * 3600
    assert 'detached' in caplog.text and 'request-a' in caplog.text
    store.record_inbound.assert_not_called()
    store.mark_synced.assert_not_called()


@pytest.mark.parametrize('headers,body_meta,expected', [
    ({'Retry-After':'900'}, None, 900),
    ({}, {'haraj':{'retryAfterSeconds':120}}, 120),
    ({'Retry-After':'999999'}, None, 3600),
    ({'Retry-After':'soon'}, None, 60),
    ({}, None, 60),
])
def test_rate_limit_backs_off_for_retry_after(monkeypatch, headers, body_meta, expected):
    body = {'errors':[{'code':'HARAJ_RATE_LIMITED'}]}
    if body_meta:
        body['meta'] = body_meta
    store, http = _sync_with_service_response(monkeypatch, httpx.Response(429, json=body, headers=headers))
    assert http.post.call_count == 1
    kwargs = store.thread_checked.call_args.kwargs
    assert kwargs['failure_code'] == 'HARAJ_RATE_LIMITED'
    assert kwargs['retry_seconds'] == expected


def test_parked_conversation_is_not_due_until_original_account_reconnects(tmp_path):
    from datetime import datetime, timezone
    from farq.store import Store
    from farq.contracts import RequestRecipient
    from farq.haraj_broker import DETACHED_CODE, DETACHED_RECHECK_SECONDS, reattach_after_verify
    store = Store(tmp_path/'detached.sqlite3', tmp_path/'uploads')
    owner = store.register('detached@example.test', 'synthetic')
    store._connection.execute('update users set farq_user_id = ? where id = ?', (OWNER, owner))
    request = store.create_request(owner, 'باب', 'باب', None, 'الرياض', {}, [RequestRecipient(seller_id='haraj:seller:202', seller_name='تجريبي')], supplier_message='بكم الباب؟')
    store._connection.execute("update haraj_threads set haraj_conversation_id = 'p2p101_202', haraj_account_id = '101' where request_id = ?", (request,))
    store._connection.commit()
    now = datetime.now(timezone.utc).timestamp()
    thread = next(t for t in store.threads_to_sync(now=now) if t['request_id'] == request)
    store.thread_checked(thread, failure_code=DETACHED_CODE, retry_seconds=DETACHED_RECHECK_SECONDS, now=now)
    assert not [t for t in store.threads_to_sync(now=now + 3600) if t['request_id'] == request]
    # A different account, another owner, or a non-connected verify leaves it parked.
    assert reattach_after_verify(store, OWNER, {'status':'CONNECTED','provider_user_id':'999'}) == 0
    assert reattach_after_verify(store, '22222222-2222-4222-8222-222222222222', {'status':'CONNECTED','provider_user_id':'101'}) == 0
    assert reattach_after_verify(store, OWNER, {'status':'REAUTH_REQUIRED','provider_user_id':'101'}) == 0
    assert not [t for t in store.threads_to_sync(now=now + 3600) if t['request_id'] == request]
    assert reattach_after_verify(store, OWNER, {'status':'CONNECTED','provider_user_id':101}) == 1
    due = [t for t in store.threads_to_sync() if t['request_id'] == request]
    assert due and due[0]['failure_code'] is None
