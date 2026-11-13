"""Real retention check in a rolled-back transaction; no emails or retained fixtures."""
import json
import uuid
from verify_data import CONFIG,secret,connect,scalar
sid=str(uuid.uuid4())
with connect('LT_OWNER',secret(CONFIG['credential_secret_ids']['database-owner'])) as db:
    try:
        with db.cursor() as c:
            for action,days in [('kafka.snapshot',3),('kafka.snapshot',2),('kafka.inventory.snapshot',0),('probe.business',3)]:
                c.execute("INSERT INTO demo_events(session_id,component,action,occurred_at) VALUES(:sid,'probe',:action,SYSTIMESTAMP-NUMTODSINTERVAL(:days,'DAY'))",sid=sid,action=action,days=days)
            c.callproc('purge_telemetry')
        assert scalar(db,'SELECT COUNT(*) FROM demo_events WHERE session_id=:sid',{'sid':sid})==3
        assert scalar(db,"SELECT COUNT(*) FROM demo_events WHERE session_id=:sid AND action='probe.business'",{'sid':sid})==1
        assert scalar(db,"SELECT COUNT(*) FROM demo_events WHERE session_id=:sid AND action='kafka.snapshot'",{'sid':sid})==1
        assert scalar(db,"SELECT COUNT(*) FROM demo_events WHERE session_id=:sid AND action='kafka.inventory.snapshot'",{'sid':sid})==1
    finally:db.rollback()
    assert scalar(db,'SELECT COUNT(*) FROM demo_events WHERE session_id=:sid',{'sid':sid})==0
print(json.dumps({'retention_probe':'passed','latest_snapshot_kept':True,'recent_snapshot_kept':True,'business_event_kept':True,'fixtures_rolled_back':True}))
