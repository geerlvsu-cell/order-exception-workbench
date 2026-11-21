import unittest
from pathlib import Path
from streamlit.testing.v1 import AppTest
from orderdesk.session_repository import SessionRepository
from orderdesk.demo import build_demo
from orderdesk.rules import analyze
from orderdesk.constants import DEMO_TIME

ROOT=Path(__file__).resolve().parents[1]


class PublicAppTests(unittest.TestCase):
    def test_repository_isolation_and_transactions(self):
        a,b=SessionRepository(),SessionRepository()
        data=analyze(*build_demo(),DEMO_TIME)
        batch,_=a.save_analysis(data)
        b.save_analysis(data)
        item=a.exceptions(batch)[0]
        a.add_action(item['exception_id'],'RESOLVED','访客甲的模拟处理','甲',0)
        self.assertEqual(len(a.actions(batch)),1)
        self.assertEqual(b.actions(batch),[])
        self.assertEqual(b.metrics(batch)['open_exception_count'],107)
        self.assertEqual(a.metrics(batch)['open_exception_count'],106)
        a.close();b.close()

    def test_auto_load_and_independent_browser_sessions(self):
        first=AppTest.from_file(str(ROOT/'public_app.py'),default_timeout=20).run()
        second=AppTest.from_file(str(ROOT/'public_app.py'),default_timeout=20).run()
        self.assertEqual(len(first.exception),0)
        self.assertEqual(len(second.exception),0)
        self.assertEqual(next(x for x in first.metric if x.label=='输入订单行记录数').value,'300 条')
        r=first.session_state['public_repository']; batch=r.list_batches()[0]['batch_id']
        self.assertIsNot(r,second.session_state['public_repository'])
        item=r.exceptions(batch)[0]
        r.add_action(item['exception_id'],'IN_PROGRESS','甲的模拟动作','甲',0)
        self.assertEqual(second.session_state['public_repository'].actions(batch),[])
        first.run()
        self.assertEqual(len(first.session_state['public_repository'].actions(batch)),1)

    def test_fresh_session_resets_data(self):
        first=AppTest.from_file(str(ROOT/'public_app.py'),default_timeout=20).run()
        repo=first.session_state['public_repository']; batch=repo.list_batches()[0]['batch_id']
        e=repo.exceptions(batch)[0]
        repo.add_action(e['exception_id'],'IN_PROGRESS','刷新前的模拟记录','访客',0)
        fresh=AppTest.from_file(str(ROOT/'public_app.py'),default_timeout=20).run()
        self.assertEqual(fresh.session_state['public_repository'].actions(batch),[])

    def test_public_ui_save_and_filter(self):
        app=AppTest.from_file(str(ROOT/'public_app.py'),default_timeout=20).run()
        next(x for x in app.text_input if x.label=='订单号精确查询').set_value('SIM-1001').run()
        self.assertEqual(len(app.dataframe[0].value),2)
        next(x for x in app.text_area if x.label=='处理动作或依据').set_value('公开版模拟验收')
        next(x for x in app.selectbox if x.label=='处理状态').set_value('IN_PROGRESS')
        next(x for x in app.button if x.label=='保存记录与状态').click().run()
        self.assertEqual(len(app.exception),0)
        self.assertTrue(any('公开版模拟验收' in x.value for x in app.markdown))


if __name__=='__main__':unittest.main()
