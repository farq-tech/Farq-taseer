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
