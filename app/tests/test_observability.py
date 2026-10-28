import json
import unittest
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import HTTPException
from lasttable import api
from lasttable.observability import position_metrics,seconds,dashboard_metrics,booking_trace,kafka_messages

class ObservabilityTests(unittest.TestCase):
    def test_lag_is_partition_local_and_detects_out_of_range(self):
        parts=[{'id':0,'low':0,'high':10},{'id':1,'low':2,'high':10}]
        # Returned positions need not be in partition order. Summed subtraction
        # would incorrectly hide this lag behind an offset beyond the log end.
        offsets=[SimpleNamespace(partition=1,offset=5),SimpleNamespace(partition=0,offset=15)]
        result=position_metrics(parts,offsets)
        self.assertEqual(result['lag'],5)
        self.assertEqual(result['committed'],20)
        self.assertTrue(result['out_of_range'])

    def test_unknown_commit_never_becomes_zero_lag(self):
        result=position_metrics([{'id':0,'low':5,'high':15}],[SimpleNamespace(partition=0,offset=-1001)])
        self.assertIsNone(result['lag'])
        self.assertIsNone(result['committed'])
        self.assertEqual(result['end'],15)

    def test_durations_handle_missing_and_timezone_values(self):
        start=datetime(2026,10,27,10,0)
        self.assertEqual(seconds(start,start.replace(tzinfo=timezone.utc)+timedelta(seconds=3)),3)
        self.assertIsNone(seconds(start,None))
        self.assertEqual(seconds(start,start-timedelta(seconds=1)),0)

    def test_dashboard_separates_session_counts_and_cluster_measurements(self):
        topic='lasttable.bookings.created'
        values=[[{'topic':topic,'queued':2,'published':1,'pending':1}],
                [{'processor_name':'confirmation','processed':1}],[],[],[],[],[]]
        with patch('lasttable.observability.rows',side_effect=values), \
             patch('lasttable.observability.one',side_effect=[{'total':0},None]):
            result=dashboard_metrics(None,'example-session')
        self.assertEqual(len(result['topics']),4)
        self.assertEqual(result['topics'][0]['session']['published'],1)
        self.assertIsNone(result['topics'][0]['cluster'])
        self.assertIsNone(result['inventory'])
        self.assertEqual(len(result['throughput']),15)
        self.assertEqual(result['lag_history'],[])

    def test_public_trace_does_not_query_personal_fields(self):
        booking={'booking_id':'private-id','public_alias':'LT-012345ABCDEF','booking_state':'PENDING',
                 'created_at':datetime.now(timezone.utc)-timedelta(seconds=20)}
        with patch('lasttable.observability.one',return_value=booking),patch('lasttable.observability.rows',side_effect=[[],[],[]]) as query:
            result=booking_trace(None,'session','LT-012345ABCDEF')
        self.assertNotIn('booking_id',result['booking'])
        self.assertGreaterEqual(result['timings']['human_wait_seconds'],20)
        self.assertEqual(result['decision_origin'],'pending')
        for call in query.call_args_list:
            self.assertNotIn('recipient_email',call.args[1])
            self.assertNotIn('nickname',call.args[1])
        self.assertNotIn('recipient_email',json.dumps(result,default=str))

    def test_message_projection_is_bounded_scoped_and_excludes_raw_payload(self):
        record={'event_id':'event','event_type':'booking.created','topic':'lasttable.bookings.created',
                'created_at':None,'published_at':'2026-10-28T10:00:00Z','partition_id':0,'source_offset':7,
                'correlation_id':'correlation','schema_version':1,'public_alias':'LT-012345ABCDEF',
                'restaurant_id':'aurora','event_state':'PENDING','state_version':0,'occurred_at':'2026-10-28T10:00:00Z',
                'confirmation_at':None,'trending_at':'2026-10-28T10:00:01Z','notification_state':None,
                'attempt_count':None,'notification_at':None,'payload':'must never escape','nickname':'private'}
        with patch('lasttable.observability.rows',return_value=[record]) as query:
            result=kafka_messages(None,'current-session')
        sql=query.call_args.args[1]
        self.assertIn('FETCH FIRST 100 ROWS ONLY',sql)
        self.assertIn('e.session_id=:sid',sql)
        self.assertEqual(query.call_args.kwargs['sid'],'current-session')
        self.assertNotIn('SELECT *',sql)
        message=result['messages'][0]
        self.assertEqual(message['offset'],7)
        self.assertEqual(message['effects']['confirmation']['state'],'UNOBSERVED')
        self.assertEqual(message['effects']['trending']['state'],'RECORDED')
        self.assertNotIn('payload',json.dumps(result,default=str))
        self.assertNotIn('nickname',json.dumps(result,default=str))
        self.assertNotIn('booking_id',message['content'])
        record['topic']='lasttable.bookings.status-changed'
        record['notification_state']='UNKNOWN'
        with patch('lasttable.observability.rows',return_value=[record]):
            message=kafka_messages(None,'current-session')['messages'][0]
        self.assertEqual(message['effects']['notifications']['state'],'UNKNOWN')
        self.assertNotIn('confirmation',message['effects'])

    def test_public_trace_is_scoped_to_current_session_and_validates_alias(self):
        with self.assertRaises(HTTPException) as error:api.public_booking_trace('../invalid')
        self.assertEqual(error.exception.status_code,404)
        with patch('lasttable.observability.one',return_value=None):
            self.assertIsNone(booking_trace(None,'another-session','LT-012345ABCDEF'))

if __name__=='__main__':unittest.main()
