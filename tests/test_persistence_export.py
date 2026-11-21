import csv
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from orderdesk.constants import DEMO_TIME
from orderdesk.demo import build_demo
from orderdesk.rules import analyze, filter_exceptions
from orderdesk.repository import Repository, StateConflict
from orderdesk.export_service import export_csv, META, EXCEPTION_FIELDS
from tests.test_import import line
from tests.test_rules import calculate


class PersistenceExportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'workbench.sqlite3'
        self.repo=Repository(self.path)
        self.analysis=calculate([line(),line(line_id='20',ordered_qty=4)])
        self.batch_id,_=self.repo.save_analysis(self.analysis)

    def tearDown(self):
        self.temp.cleanup()

    def test_repeat_import_keeps_actions(self):
        e=self.repo.exceptions(self.batch_id)[0]
        self.repo.add_action(e['exception_id'],'IN_PROGRESS','已联系仓库','演示用户',0)
        self.assertEqual(self.repo.save_analysis(self.analysis),(self.batch_id,False))
        self.assertEqual(len(self.repo.list_batches()),1)
        self.assertEqual(len(self.repo.exceptions(self.batch_id)),3)
        self.assertEqual(len(self.repo.actions(self.batch_id)),1)
        saved=next(x for x in self.repo.exceptions(self.batch_id) if x['exception_id']==e['exception_id'])
        self.assertEqual(saved['processing_status'],'IN_PROGRESS')

    def test_reopen_and_separate_process_reads_saved_state(self):
        e=self.repo.exceptions(self.batch_id)[0]
        self.repo.add_action(e['exception_id'],'RESOLVED','已确认处理完成','用户',0)
        reopened=Repository(self.path)
        self.assertEqual(reopened.metrics(self.batch_id)['open_exception_count'],2)
        self.assertEqual(reopened.metrics(self.batch_id)['overdue_line_count'],2)
        script='import sys,json; from orderdesk.repository import Repository; r=Repository(sys.argv[1]); print(json.dumps(r.actions(sys.argv[2]),ensure_ascii=False))'
        result=subprocess.run([sys.executable,'-c',script,str(self.path),self.batch_id],capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(result.stdout)[0]['action_text'],'已确认处理完成')

    def test_new_batch_does_not_cross_link(self):
        old=self.repo.exceptions(self.batch_id)[0]
        self.repo.add_action(old['exception_id'],'RESOLVED','旧批次已处理','用户',0)
        newer=calculate([line(),line(line_id='20',ordered_qty=5)])
        new_id,created=self.repo.save_analysis(newer)
        self.assertTrue(created)
        self.assertNotEqual(new_id,self.batch_id)
        self.assertFalse(set(x['exception_id'] for x in self.repo.exceptions(new_id)) & {old['exception_id']})
        self.assertEqual(self.repo.actions(new_id),[])
        self.assertTrue(all(x['processing_status']=='TODO' for x in self.repo.exceptions(new_id)))

    def test_required_note_transition_and_stale_form(self):
        e=self.repo.exceptions(self.batch_id)[0]
        with self.assertRaises(ValueError):
            self.repo.add_action(e['exception_id'],'RESOLVED',' ','用户',0)
        self.repo.add_action(e['exception_id'],'RESOLVED','已完成','用户',0)
        with self.assertRaises(StateConflict):
            self.repo.add_action(e['exception_id'],'IN_PROGRESS','旧表单','用户',0)
        with self.assertRaises(ValueError):
            self.repo.add_action(e['exception_id'],'IGNORED','不允许直接转换','用户',1)
        self.repo.add_action(e['exception_id'],'IN_PROGRESS','收到新反馈，重新跟进','用户',1)
        self.repo.add_action(e['exception_id'],'IN_PROGRESS','补充记录','用户',2)
        self.assertEqual(len(self.repo.actions(self.batch_id)),3)

    def test_transaction_failure_rolls_back_action_and_state(self):
        e=self.repo.exceptions(self.batch_id)[0]
        with self.repo.connection() as db:
            db.execute("CREATE TRIGGER fail_update BEFORE UPDATE ON exceptions BEGIN SELECT RAISE(ABORT,'simulated failure'); END")
        with self.assertRaises(sqlite3.Error):
            self.repo.add_action(e['exception_id'],'RESOLVED','不应部分保存','用户',0)
        self.assertEqual(self.repo.actions(self.batch_id),[])
        self.assertTrue(all(x['processing_status']=='TODO' for x in self.repo.exceptions(self.batch_id)))

    def test_transaction_failure_rolls_back_entire_batch(self):
        with self.repo.connection() as db:
            db.execute("CREATE TRIGGER fail_insert BEFORE INSERT ON exceptions BEGIN SELECT RAISE(ABORT,'simulated failure'); END")
        a=calculate([line(order_id='SIM-NEW')])
        with self.assertRaises(sqlite3.Error):
            self.repo.save_analysis(a)
        self.assertEqual(len(self.repo.list_batches()),1)
        with self.repo.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM raw_rows WHERE batch_id=?',(a.batch['batch_id'],)).fetchone()[0],0)

    def test_foreign_keys_and_uniqueness(self):
        with self.repo.connection() as db:
            self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0],1)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO action_logs(exception_id,action_text,from_status,to_status,actor_display_name,acted_at) VALUES ('unknown','x','TODO','TODO','test','2026-11-21 10:00:00')")

    def test_filtered_export_metadata_and_action_association(self):
        items=self.repo.exceptions(self.batch_id)
        selected=filter_exceptions(items,types=['STOCK_GAP'])
        e=selected[0]
        self.repo.add_action(e['exception_id'],'IN_PROGRESS','首次,联系\n保留换行','用户',0)
        self.repo.add_action(e['exception_id'],'RESOLVED','二次确认','用户',1)
        other=next(x for x in items if x['exception_id']!=e['exception_id'])
        self.repo.add_action(other['exception_id'],'IN_PROGRESS','另一项记录不可串入','用户',0)
        fresh=filter_exceptions(self.repo.exceptions(self.batch_id),types=['STOCK_GAP'])
        data=export_csv('exceptions',fresh,self.repo.batch(self.batch_id),filters={'types':['STOCK_GAP']})
        self.assertTrue(data.startswith(b'\xef\xbb\xbf'))
        rows=list(csv.DictReader(io.StringIO(data.decode('utf-8-sig'))))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['data_mode'],'SIMULATED')
        self.assertEqual(rows[0]['processing_status'],'RESOLVED')
        self.assertEqual(rows[0]['order_id'],'')
        self.assertEqual(json.loads(rows[0]['evidence_json'])['gap_qty'],2)
        actions=self.repo.actions(self.batch_id,[e['exception_id']])
        exported=list(csv.DictReader(io.StringIO(export_csv('actions',actions,self.repo.batch(self.batch_id)).decode('utf-8-sig'))))
        self.assertEqual(len(exported),2)
        self.assertEqual(exported[0]['action_text'],'首次,联系\n保留换行')

    def test_unknown_stock_null_and_empty_headers(self):
        a=calculate([line(warehouse_id='WH-BJ')])
        rows=list(csv.DictReader(io.StringIO(export_csv('inventory',a.inventory_checks,a.batch,invalid_order_rows=2).decode('utf-8-sig'))))
        self.assertEqual(rows[0]['available_qty'],'')
        self.assertEqual(rows[0]['gap_qty'],'')
        self.assertEqual(rows[0]['supply_status'],'UNKNOWN_MISSING')
        self.assertEqual(rows[0]['batch_invalid_order_row_count'],'2')
        reader=csv.DictReader(io.StringIO(export_csv('exceptions',[],a.batch).decode('utf-8-sig')))
        self.assertEqual(reader.fieldnames,META+EXCEPTION_FIELDS)
        self.assertEqual(list(reader),[])

    def test_300_row_dataset_roundtrip(self):
        o,i=build_demo()
        a=analyze(o,i,DEMO_TIME)
        batch_id,_=self.repo.save_analysis(a)
        self.assertEqual(self.repo.metrics(batch_id)['input_order_row_count'],300)
        self.assertEqual(len(self.repo.exceptions(batch_id)),107)
        self.assertEqual(len(self.repo.inventory_checks(batch_id)),18)


if __name__=='__main__':
    unittest.main()
