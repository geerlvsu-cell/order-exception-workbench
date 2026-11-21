"""预期来自预先保存的人工计算案例，不用被测规则生成预期。"""
import csv, io, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from orderdesk.rules import analyze, filter_exceptions
from orderdesk.repository import Repository
from orderdesk.export_service import export_csv
from streamlit.testing.v1 import AppTest

ROOT=Path(__file__).resolve().parents[1]
FIX=ROOT/'outputs/本轮验收'

class IndependentAcceptance(unittest.TestCase):
    def setUp(self):
        self.fixture=json.loads((FIX/'独立验收预期.json').read_text())
        self.orders=(FIX/'独立验收_订单.csv').read_bytes()
        self.stock=(FIX/'独立验收_库存.csv').read_bytes()
        self.a=analyze(self.orders,self.stock,self.fixture['analysis_at'])

    def test_hand_calculated_metrics(self):
        for key,value in self.fixture['expected']['metrics'].items():
            self.assertEqual(self.a.metrics[key],value,key)

    def test_exact_overdue_objects_and_exclusions(self):
        actual=sorted([e['order_id'],e['line_id']] for e in self.a.exceptions if e['exception_type']=='OVERDUE')
        self.assertEqual(actual,sorted(self.fixture['expected']['overdue_lines']))

    def test_quality_objects_and_reason_codes(self):
        actual={e['order_id']:e['reason_codes'] for e in self.a.exceptions if e['exception_type']=='DATA_QUALITY'}
        self.assertEqual(set(actual),set(self.fixture['expected']['quality_orders']))
        self.assertIn('DATETIME_FORMAT_INVALID',actual['BADDATE'])
        self.assertIn('FIELD_REQUIRED',actual['MISSING'])
        self.assertIn('QTY_OUT_OF_RANGE',actual['BADQ'])

    def test_warehouse_sku_demand_and_gap(self):
        actual=[[x[k] for k in ['warehouse_id','sku_id','demand_qty','available_qty','gap_qty']] for x in self.a.inventory_checks]
        self.assertEqual(actual,self.fixture['expected']['stock'])
        risk=[e for e in self.a.exceptions if e['exception_type']=='STOCK_GAP']
        self.assertEqual(len(risk),1)
        self.assertIsNone(risk[0]['order_id'])

    def test_repeat_import_and_process_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'test.sqlite3';repo=Repository(path)
            batch,created=repo.save_analysis(self.a)
            item=next(e for e in repo.exceptions(batch) if e['exception_type']=='OVERDUE' and e['order_id']=='L1' and e['line_id']=='20')
            repo.add_action(item['exception_id'],'IN_PROGRESS','独立验收模拟动作','验收人',0)
            self.assertEqual(repo.save_analysis(self.a),(batch,False))
            self.assertEqual(len(repo.exceptions(batch)),8)
            script='import sys,json;from orderdesk.repository import Repository;r=Repository(sys.argv[1]);print(json.dumps(r.actions(sys.argv[2]),ensure_ascii=False))'
            rows=json.loads(subprocess.check_output([sys.executable,'-c',script,str(path),batch],text=True))
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['exception_id'],item['exception_id'])
            self.assertEqual(rows[0]['to_status'],'IN_PROGRESS')

    def test_export_exact_filtered_objects(self):
        selected=filter_exceptions(self.a.exceptions,types=['OVERDUE'],order_id='L1')
        data=export_csv('exceptions',selected,self.a.batch,filters={'types':['OVERDUE'],'order_id':'L1'})
        rows=list(csv.DictReader(io.StringIO(data.decode('utf-8-sig'))))
        self.assertEqual(sorted(r['line_id'] for r in rows),self.fixture['expected']['filtered_L1'])
        self.assertEqual({r['order_id'] for r in rows},{'L1'})

    def test_page_filter_matches_independent_expected(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'ORDER_DESK_DB':str(Path(tmp)/'app.sqlite3')}):
            at=AppTest.from_file(str(ROOT/'app.py'),default_timeout=20).run()
            at.radio('import_mode').set_value('上传模拟 CSV').run()
            at.file_uploader('order_upload').set_value(('独立验收_订单.csv',self.orders,'text/csv'))
            at.file_uploader('stock_upload').set_value(('独立验收_库存.csv',self.stock,'text/csv')).run()
            next(x for x in at.button if x.label=='检查数据').click().run()
            next(x for x in at.button if x.label=='保存批次并进入工作台').click().run()
            next(x for x in at.multiselect if x.label=='异常类型').set_value(['OVERDUE']).run()
            next(x for x in at.text_input if x.label=='订单号精确查询').set_value('L1').run()
            self.assertEqual(len(at.exception),0)
            self.assertEqual(len(at.dataframe[0].value),2)
            self.assertTrue(all('L1' in x for x in at.dataframe[0].value['对象']))

    def test_changed_input_clears_obsolete_error(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'ORDER_DESK_DB':str(Path(tmp)/'app.sqlite3')}):
            at=AppTest.from_file(str(ROOT/'app.py'),default_timeout=20).run()
            at.text_input('analysis_at_input').set_value('不是日期').run()
            next(x for x in at.button if x.label=='检查数据').click().run()
            self.assertEqual(len(at.error),1)
            at.text_input('analysis_at_input').set_value('2026-11-21 10:00:00').run()
            self.assertEqual(len(at.error),0)
            self.assertFalse(any(x.label=='保存批次并进入工作台' for x in at.button))

if __name__=='__main__':unittest.main()
