"""Notification lease + conservative SMTP outcome handling.

UNKNOWN is persisted before SMTP DATA. A crash or lost final SMTP reply can never
cause an automatic duplicate email; UNKNOWN requires manual reconciliation.
"""
import smtplib
import ssl
import uuid

import oracledb
from .mail import booking_message
from .runtime import config, database, execute, log, one, secret


def result(state, attempts=0):
    return {'state': state, 'attempts': attempts}


def notify(event):
    for key in ('event_id', 'booking_id', 'session_id'):
        uuid.UUID(event[key])
    receipt = event.get('event_type')=='booking.received' and event.get('state')=='PENDING'
    decision = event.get('event_type')=='booking.status_changed' and event.get('state') in ('CONFIRMED','REJECTED')
    if not (receipt or decision):
        raise ValueError('NOT_A_BOOKING_NOTIFICATION')
    lease = str(uuid.uuid4())
    with database('notify') as db:
        recipient = one(db, 'SELECT * FROM LT_OWNER.booking_recipients WHERE booking_id=:b_id', b_id=event['booking_id'])
        if not recipient or recipient['session_id'] != event['session_id']:
            raise ValueError('RECIPIENT_NOT_FOUND')
        try:
            execute(db, '''INSERT INTO LT_OWNER.notification_delivery
                (event_id,recipient_ref,channel,booking_id,notification_state)
                VALUES(:b_event,:b_ref,'email',:b_ref,'PENDING')''', b_event=event['event_id'], b_ref=event['booking_id'])
            db.commit()
        except oracledb.IntegrityError as error:
            if error.args[0].code != 1:
                raise
            db.rollback()
        ledger = one(db, '''SELECT notification_state,attempt_count,
            CASE WHEN lease_expires_at>SYSTIMESTAMP THEN 1 ELSE 0 END AS busy
            FROM LT_OWNER.notification_delivery WHERE event_id=:b_event AND recipient_ref=:b_ref AND channel='email' FOR UPDATE''',
            b_event=event['event_id'], b_ref=event['booking_id'])
        state = ledger['notification_state']
        if state in ('SUBMITTED', 'UNKNOWN') or ledger['attempt_count'] >= 3:
            log(db,event,'function','event.duplicate_skipped',outcome=state,detail='Invio già registrato o sospeso; nessun nuovo tentativo SMTP')
            db.commit()
            return result(state, ledger['attempt_count'])
        if ledger['busy']:
            return result('PENDING', ledger['attempt_count'])
        execute(db, '''UPDATE LT_OWNER.notification_delivery SET notification_state='PENDING',
            lease_token=:b_lease,lease_expires_at=SYSTIMESTAMP+INTERVAL '90' SECOND,
            attempt_count=attempt_count+1,error_code=NULL,updated_at=SYSTIMESTAMP
            WHERE event_id=:b_event AND recipient_ref=:b_ref AND channel='email' ''',
            b_lease=lease, b_event=event['event_id'], b_ref=event['booking_id'])
        db.commit()
    attempts = ledger['attempt_count']+1
    cfg = config()
    message = booking_message(recipient, event, cfg['sender'])
    message_id = message['Message-ID']
    smtp = None
    phase = 'SETUP'
    state, error_code = 'FAILED', None
    try:
        smtp = smtplib.SMTP(cfg['smtp_host'], int(cfg.get('smtp_port', 587)), timeout=10)
        smtp.ehlo()
        if not cfg.get('smtp_local_capture', False):
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(secret('smtp-username'), secret('smtp-password'))
        code, _ = smtp.mail(cfg['sender'])
        if code != 250:
            raise smtplib.SMTPResponseException(code, b'MAIL rejected')
        code, _ = smtp.rcpt(recipient['recipient_email'])
        if code not in (250, 251):
            raise smtplib.SMTPResponseException(code, b'RCPT rejected')
        # Durable uncertainty fence: no email is sent until this commit succeeds.
        with database('notify') as db:
            changed = execute(db, '''UPDATE LT_OWNER.notification_delivery SET notification_state='UNKNOWN',
                message_id=:b_message,error_code='SMTP_DATA_IN_FLIGHT',updated_at=SYSTIMESTAMP
                WHERE event_id=:b_event AND recipient_ref=:b_ref AND channel='email' AND lease_token=:b_lease AND lease_expires_at>SYSTIMESTAMP''',
                b_message=message_id, b_event=event['event_id'], b_ref=event['booking_id'], b_lease=lease)
            if changed != 1:
                raise RuntimeError('LEASE_EXPIRED')
            db.commit()
        phase = 'DATA'
        code, _ = smtp.data(message.as_bytes())
        if code != 250:
            raise smtplib.SMTPDataError(code, b'DATA rejected')
        state = 'SUBMITTED'
    except smtplib.SMTPResponseException as error:
        state, error_code = 'FAILED', 'SMTP_'+str(error.smtp_code)
    except Exception:
        state = 'UNKNOWN' if phase=='DATA' else 'FAILED'
        error_code = 'SMTP_REPLY_UNCERTAIN' if state=='UNKNOWN' else 'SMTP_SETUP_FAILED'
    finally:
        if smtp:
            try:
                smtp.close()
            except OSError:
                pass
    with database('notify') as db:
        execute(db, '''UPDATE LT_OWNER.notification_delivery SET notification_state=:b_state,error_code=:b_error,
            message_id=:b_message,lease_expires_at=NULL,updated_at=SYSTIMESTAMP,
            smtp_submitted_at=CASE WHEN :b_state='SUBMITTED' THEN SYSTIMESTAMP ELSE smtp_submitted_at END
            WHERE event_id=:b_event AND recipient_ref=:b_ref AND channel='email' AND lease_token=:b_lease''',
            b_state=state, b_error=error_code, b_message=message_id,
            b_event=event['event_id'], b_ref=event['booking_id'], b_lease=lease)
        log(db, event, 'function', 'notification.'+state.lower(), outcome=state,
            detail={'SUBMITTED':'SMTP ha accettato il messaggio; recapito non ancora verificato',
                    'UNKNOWN':'Esito SMTP ambiguo: invio automatico sospeso', 'FAILED':'Invio SMTP non accettato'}[state])
        db.commit()
    return result(state, attempts)
