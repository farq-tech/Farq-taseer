"""No provider calls or customer writes while per-user messaging lacks evidence."""
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from farq.api import create_app
from farq.config import SearchConfig
from farq.corpus import MemoryCorpus, default_sample_path
from farq.haraj_chat import HarajChatClient, HarajChatUnavailable, HarajSession, NotConnectedChat, chat_from_env
from farq.haraj_user_connection import EVIDENCE_REQUIRED, enabled
from farq.store import Store
from farq.worker import dispatch_pending, sync_replies


@pytest.mark.parametrize('value,expected', [('0',False), ('1',True), ('true',True), ('false',False)])
def test_flag(value, expected):
    assert enabled({'HARAj_USER_ACCOUNT_CONNECTION': value}) is expected


def test_central_factory_cannot_be_enabled():
    chat = chat_from_env({'HARAj_USER_ACCOUNT_CONNECTION': '1', 'HARAJ_SEND_ENABLED': '1', 'HARAJ_INBOX_ENABLED': '1'})
    assert isinstance(chat, NotConnectedChat)


def test_running_workers_stop_before_claim_or_sync(monkeypatch):
    monkeypatch.setenv('HARAj_USER_ACCOUNT_CONNECTION', '1')
    store, chat = Mock(), Mock()
    assert dispatch_pending(store, chat) == 0
    assert sync_replies(store, chat) == 0
    assert not store.mock_calls and not chat.mock_calls


def test_preexisting_client_and_session_cannot_use_cached_central_token(monkeypatch):
    session = HarajSession({})
    session._current = ('synthetic-never-a-real-token', 9999999999)
    http = Mock()
    client = HarajChatClient(session, '900000001', http=http, socket_factory=Mock())
    monkeypatch.setenv('HARAj_USER_ACCOUNT_CONNECTION', '1')
    with pytest.raises(HarajChatUnavailable, match='HARAJ_USER_CONNECTION_REQUIRED'):
        session.access_token()
    with pytest.raises(HarajChatUnavailable, match=EVIDENCE_REQUIRED):
        client.send(conversation_id=None, seller_id='synthetic-seller', ad_id=None, body='test')
    with pytest.raises(HarajChatUnavailable, match=EVIDENCE_REQUIRED):
        client.fetch(conversation_id='synthetic-topic', seller_id='synthetic-seller', after_seq=0)
    assert not http.mock_calls


def test_http_gate_before_request_message_attachment_or_billing(tmp_path: Path, monkeypatch):
    monkeypatch.setenv('FARQ_RECIPIENTS_FROM_SEARCH', '0')
    store = Store(tmp_path/'test.sqlite3', tmp_path/'uploads')
    app = create_app(store, MemoryCorpus.from_json(default_sample_path()), None, SearchConfig(enable_live=False))
    api = TestClient(app)
    login = api.post('/v1/auth/register', json={'email':'synthetic@example.com','password':'synthetic-password','name':'اختبار'})
    headers = {'Authorization': 'Bearer '+login.json()['token']}
    monkeypatch.setenv('HARAj_USER_ACCOUNT_CONNECTION', '1')
    cases = [
        ('/v1/requests', {'original_text':'طلب اختبار', 'need':'نجار', 'city':'الرياض', 'recipients':[{'seller_id':'synthetic-seller','seller_name':'اختبار'}]}),
        ('/v1/requests/synthetic-request/messages', {'body':'اختبار'}),
        ('/v1/requests/synthetic-request/counter', {'seller_id':'synthetic-seller','amount':100}),
        ('/v1/requests/synthetic-request/award', {'seller_id':'synthetic-seller'}),
    ]
    store.create_request = Mock(side_effect=AssertionError('must not write'))
    store.route_customer_message = Mock(side_effect=AssertionError('must not write'))
    for path, body in cases:
        response = api.post(path, headers=headers, json=body)
        assert response.status_code == 501, response.text
        assert response.json()['detail']['code'] == EVIDENCE_REQUIRED
    response = api.post('/v1/requests/synthetic-request/attachments', headers=headers, files={'file':('test.jpg',b'test','image/jpeg')})
    assert response.status_code == 501
    assert store.list_requests(store.user_for_token(login.json()['token'])) == []
    store.create_request.assert_not_called()
    store.route_customer_message.assert_not_called()


def test_gate_requires_farq_auth_first(tmp_path: Path, monkeypatch):
    app = create_app(Store(tmp_path/'test.sqlite3',tmp_path/'uploads'), MemoryCorpus.from_json(default_sample_path()), None, SearchConfig(enable_live=False))
    monkeypatch.setenv('HARAj_USER_ACCOUNT_CONNECTION','1')
    response = TestClient(app).post('/v1/requests', json={'original_text':'اختبار', 'recipients':[]})
    assert response.status_code == 401


def test_buyer_authored_message_is_preserved_without_company_invite(tmp_path):
    from farq.contracts import RequestRecipient
    store = Store(tmp_path/'message.sqlite3', tmp_path/'uploads')
    text = 'السلام عليكم، بكم 25 باب PVC سماكة 6 سم؟\nوالتوصيل للرياض؟'
    request_id = store.create_request('synthetic-owner','25 باب PVC','باب PVC',None,'الرياض',{},[RequestRecipient(seller_id='900000002',seller_name='synthetic')],supplier_message=text)
    messages = store._connection.execute('select body, haraj_text from messages where request_id = ?', (request_id,)).fetchall()
    assert len(messages) == 1
    assert messages[0]['body'] == text == messages[0]['haraj_text']
    assert 'فرق' not in messages[0]['haraj_text'] and 'https://' not in messages[0]['haraj_text']
