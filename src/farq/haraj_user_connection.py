"""Per-user Haraj release boundary. No central credentials or browser-selected owner."""
import os
from fastapi import HTTPException

EVIDENCE_REQUIRED = 'HARAJ_MESSAGING_NOT_READY'

def enabled(env=None) -> bool:
    values = os.environ if env is None else env
    return str(values.get('HARAj_USER_ACCOUNT_CONNECTION','0')).strip().lower() in ('1','true')

def require_messaging_evidence(store=None, user_id=None) -> None:
    if not enabled():
        return
    from farq.haraj_chat import HarajChatUnavailable
    from farq.haraj_broker import UserHarajBroker
    broker = UserHarajBroker()
    if not broker.configured or store is None or user_id is None:
        raise HTTPException(503, detail={'code':EVIDENCE_REQUIRED,'message':'مراسلات حراج غير متاحة حاليًا داخل فرق'})
    farq_owner = store.farq_user_id(user_id)
    try:
        result = broker.call('verify',farq_owner)
    except HarajChatUnavailable as error:
        raise HTTPException(409, detail={'code':error.code,'message':'حساب حراج يحتاج إعادة تحقق'}) from None
    if result.get('status') != 'CONNECTED':
        raise HTTPException(409, detail={'code':'HARAJ_REAUTH_REQUIRED','message':'اربط حسابك في حراج قبل إرسال الطلب'})
    from farq.haraj_broker import reattach_after_verify
    reattach_after_verify(store, farq_owner, result)
    return result
