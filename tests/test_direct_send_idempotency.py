import asyncio
from unittest.mock import Mock
import pytest
from farq.idempotency import IdempotencyMiddleware

@pytest.mark.parametrize('outcome',['exception','server-error'])
def test_unknown_direct_action_keeps_owner_reservation(monkeypatch,outcome):
    monkeypatch.setenv('HARAj_USER_ACCOUNT_CONNECTION','1')
    store=Mock();store.user_for_token.return_value='owner-a';store.reserve_idempotency.return_value=None
    async def app(scope,receive,send):
        if outcome=='exception':raise RuntimeError('synthetic interruption')
        await send({'type':'http.response.start','status':500,'headers':[]})
        await send({'type':'http.response.body','body':b'{}'})
    async def receive():return {'type':'http.request','body':b'{}','more_body':False}
    async def send(_):pass
    scope={'type':'http','method':'POST','path':'/v1/requests','headers':[(b'authorization',b'Bearer synthetic'),(b'idempotency-key',b'synthetic-action')]}
    middleware=IdempotencyMiddleware(app,store)
    if outcome=='exception':
        with pytest.raises(RuntimeError):asyncio.run(middleware(scope,receive,send))
    else:asyncio.run(middleware(scope,receive,send))
    store.release_idempotency.assert_not_called()
    assert store.reserve_idempotency.call_args.args[:3]==('owner-a','/v1/requests','synthetic-action')
