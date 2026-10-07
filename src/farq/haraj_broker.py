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
        attributes = item.get('consent_attributes') or '{}'
        attributes = json.loads(attributes) if isinstance(attributes,str) else attributes
        consent = attributes.get('_haraj_consent',{})
        recipients = consent.get('recipients',[])
        if consent.get('owner') != owner or item['seller_id'] not in recipients or not consent.get('at'):
            raise HarajChatUnavailable('HARAJ_CONSENT_REQUIRED')
        recipient = item['seller_id']
        if recipient.startswith('haraj:seller:'):
            recipient = recipient[len('haraj:seller:'):]
        if not recipient.isdigit() or int(recipient) < 1:
            raise HarajChatUnavailable('HARAJ_INVALID_RECIPIENT')
        data = self.call('send', owner, {
            'rfqId':str(uuid.UUID(item['request_id'])), 'deliveryId':item['id'],
            'itemId':item.get('need') or '', 'supplierId':item['seller_id'],
            'adId':item.get('ad_id'), 'recipientId':recipient, 'expectedAccountId':consent.get('account'),
            'conversationId':item.get('haraj_conversation_id'), 'text':item['body'],
            'consentAt':consent['at'], 'recipientCount':len(recipients),
        })
        if data.get('status') != 'ACCEPTED':
            raise HarajChatUnavailable('HARAJ_SEND_UNCERTAIN')
        return SentMessage(data['conversation_id'], data['message_id'], data['seq'], data['account_id'])

    def read_thread(self, store, thread, after_seq):
        if not thread.get('haraj_account_id'):
            raise HarajChatUnavailable('HARAJ_CONVERSATION_OWNERSHIP')
        owner = store.farq_user_for_request(thread['request_id'])
        recipient = str(thread['seller_id'])
        if recipient.startswith('haraj:seller:'):
            recipient = recipient[len('haraj:seller:'):]
        data = self.call('messages', owner, {
            'recipientId':recipient,'conversationId':thread['haraj_conversation_id'],
            'expectedAccountId':thread.get('haraj_account_id'),'afterSeq':after_seq,
        })
        account = str(data.get('account_id',''))
        if account != str(thread.get('haraj_account_id')):
            raise HarajChatUnavailable('HARAJ_CONVERSATION_OWNERSHIP')
        return [InboundMessage(item['message_id'],item['text'],item['sent_at'],item['seq'])
                for item in data.get('messages',[]) if item['sender_id'] != account]


def send_request_directly(store, request_id, local_owner):
    """Send only the authenticated request now; no global drain or automatic retry."""
    if store.get_request(request_id, local_owner) is None:
        raise HarajChatUnavailable('HARAJ_CONVERSATION_OWNERSHIP')
    broker = UserHarajBroker()
    sent = 0
    while True:
        rows = store.claim_deliveries(limit=1, request_id=request_id)
        if not rows:
            return sent
        item = rows[0]
        try:
            receipt = broker.send_delivery(store, item)
        except Exception as error:
            code = error.code if isinstance(error, HarajChatUnavailable) else 'HARAJ_SEND_UNCERTAIN'
            store.finish_delivery(item['id'], error=code, retry=False)
            # Stop this action on refusal or unknown outcome. No remaining POST is attempted.
            while True:
                remaining = store.claim_deliveries(limit=1, request_id=request_id)
                if not remaining:
                    return sent
                store.finish_delivery(remaining[0]['id'], error='HARAJ_ACTION_STOPPED', retry=False)
        else:
            store.finish_delivery(item['id'], sent=receipt)
            sent += 1


def sync_user_replies(store, budget_seconds, clock):
    broker = UserHarajBroker()
    if not broker.configured:
        return 0
    deadline = clock()+budget_seconds
    conversations = {}
    for thread in store.threads_to_sync(now=clock()):
        owner=store.farq_user_for_request(thread['request_id'])
        key=(owner,thread['haraj_conversation_id'],thread.get('haraj_account_id'))
        conversations.setdefault(key,[]).append(thread)
    received=0
    for (owner,conversation,account),threads in conversations.items():
        if clock() >= deadline:
            break
        try:
            messages=broker.read_thread(store,threads[0],min(int(t.get('high_water') or 0) for t in threads))
        except HarajChatUnavailable as error:
            for thread in threads:
                store.thread_checked(thread,failure_code=error.code,retry_seconds=60,now=clock())
            continue
        except Exception:
            for thread in threads:
                store.thread_checked(thread,failure_code='HARAJ_PROTOCOL_UNRESOLVED',retry_seconds=60,now=clock())
            continue
        # Editable buyer messages carry no artificial reference footer. Multiple active RFQs
        # in one conversation cannot be assigned from wording/time guesses.
        high_water=max((item.seq for item in messages),default=0)
        for item in messages:
            if store.has_haraj_message(item.haraj_message_id):
                continue
            if len(threads)!=1:
                store.record_unmatched_inbound(conversation,threads[0]['seller_id'],item,farq_user_id=owner)
                continue
            thread=threads[0]
            if store.farq_user_for_request(thread['request_id'])!=owner:
                raise HarajChatUnavailable('HARAJ_CONVERSATION_OWNERSHIP')
            recorded=store.record_inbound(thread,item)
            if recorded is not None:
                received+=1
                from farq.push import notify_reply
                notify_reply(store,thread['request_id'],thread['seller_id'],item.body,message_id=recorded.id)
        # Unique provider-owner connections make this update owner-exclusive. Preserve original
        # messages and use existing quote extraction / per-user read persistence.
        store.owner_conversation_checked(owner,conversation,retry_seconds=30,now=clock(),high_water=high_water,failure_code='HARAJ_ITEM_ASSIGNMENT_REQUIRED' if len(threads)!=1 and messages else None)
        for thread in threads:
            if len(threads)==1:
                store.mark_synced(thread['request_id'])
    return received


def owned_unmatched(store, local_owner):
    farq_owner=store.farq_user_id(local_owner)
    if not farq_owner:
        return []
    result=[]
    for row in store.unmatched_inbound():
        if row.get('farq_user_id') != farq_owner:
            continue
        candidates=[]
        for request_id in row.get('candidate_request_ids') or []:
            record=store.get_request(request_id,local_owner)
            if record is None:
                continue
            for recipient in record.recipients:
                if recipient.seller_id == row['seller_id']:
                    candidates.append({'request_id':request_id,'need':recipient.need or record.need or '', 'seller_id':recipient.seller_id})
        result.append({'id':row['haraj_message_id'],'conversation_id':row['haraj_conversation_id'],'text':row['body'],'sent_at':str(row['sent_at']),'candidates':candidates})
    return result
