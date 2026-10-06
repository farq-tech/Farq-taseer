"""Server-only bridge to the Farq credential vault. No provider credentials cross it."""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import time
import uuid
import httpx
from farq.haraj_chat import HarajChatUnavailable, SentMessage, InboundMessage

class UserHarajBroker:
    def __init__(self, env=None, http=None):
        env = os.environ if env is None else env
        self.url = str(env.get('HARAJ_CONNECTION_SERVICE_URL', '')).rstrip('/')
        self.secret = str(env.get('HARAJ_CONNECTION_SERVICE_SECRET', ''))
        self.http = http or httpx.Client(timeout=35, follow_redirects=False)
        self.configured = self.url.startswith('https://') and len(self.secret) >= 32

    def call(self, operation, owner, input=None):
        if not self.configured:
            raise HarajChatUnavailable('HARAJ_CONFIGURATION_REQUIRED')
        try:
            owner = str(uuid.UUID(owner))
        except (ValueError, TypeError, AttributeError):
            raise HarajChatUnavailable('HARAJ_USER_CONNECTION_REQUIRED') from None
        payload = {'operation': operation, 'owner': owner, 'input': input or {}}
        # Match JavaScript JSON.stringify: compact UTF-8 with insertion-ordered keys.
        body = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        stamp = str(int(time.time()*1000))
        signature = hmac.new(self.secret.encode(), (stamp+'\n'+body).encode(), hashlib.sha256).hexdigest()
        try:
            response = self.http.post(self.url, content=body.encode(), headers={
                'Content-Type':'application/json','X-Haraj-Timestamp':stamp,'X-Haraj-Signature':signature,
            })
            result = response.json()
        except Exception:
            raise HarajChatUnavailable('HARAJ_SEND_UNCERTAIN' if operation == 'send' else 'HARAJ_PROVIDER_UNAVAILABLE') from None
        if not response.is_success or not isinstance(result,dict):
            # Only bounded codes pass through; no provider body or request is logged.
            errors = result.get('errors') if isinstance(result,dict) else None
            code = errors[0].get('code') if isinstance(errors,list) and errors and isinstance(errors[0],dict) else None
            allowed = {'HARAJ_RATE_LIMITED','HARAJ_REAUTH_REQUIRED','HARAJ_CHALLENGE_REQUIRED','HARAJ_SEND_UNCERTAIN','HARAJ_CONVERSATION_OWNERSHIP','HARAJ_HISTORY_INCOMPLETE','HARAJ_CONFIGURATION_REQUIRED'}
            raise HarajChatUnavailable(code if code in allowed else 'HARAJ_PROVIDER_UNAVAILABLE')
        data = result.get('data')
        if not isinstance(data,dict):
            raise HarajChatUnavailable('HARAJ_PROTOCOL_UNRESOLVED')
        return data

    def send_delivery(self, store, item):
        owner = store.farq_user_for_request(item['request_id'])
        if item.get('media'):
            raise HarajChatUnavailable('HARAJ_UNSUPPORTED_MESSAGE_CONTENT')
        data = self.call('send', owner, {
            'rfqId':str(uuid.UUID(item['request_id'])), 'deliveryId':item['id'],
            'itemId':item.get('need') or '', 'supplierId':item['seller_id'],
            'adId':item.get('ad_id'), 'recipientId':item['seller_id'],
            'conversationId':item.get('haraj_conversation_id'), 'text':item['body'],
            'consentAt':item['request_created_at'], 'recipientCount':item['recipient_count'],
        })
        if data.get('status') != 'ACCEPTED':
            raise HarajChatUnavailable('HARAJ_SEND_UNCERTAIN')
        return SentMessage(data['conversation_id'], data['message_id'], data['seq'], data['account_id'])
