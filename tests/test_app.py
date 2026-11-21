import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from orderdesk.demo import build_demo
from orderdesk.repository import Repository

ROOT=Path(__file__).resolve().parents[1]


def by_label(elements,label):
    return next(x for x in elements if x.label==label)


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=Path(self.temp.name)/'test.sqlite3'
        self.environment=patch.dict(os.environ,{'ORDER_DESK_DB':str(self.db)})
        self.environment.start()
        self.app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=20).run()

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def save_demo(self):
        by_label(self.app.button,'检查数据').click().run()
        by_label(self.app.button,'保存批次并进入工作台').click().run()
        self.assertEqual(len(self.app.exception),0)

    def test_import_filter_action_refresh_and_repeat(self):
        self.save_demo()
        self.assertEqual(self.app.radio('page_nav').value,'异常工作台')
        self.assertEqual(by_label(self.app.metric,'输入订单行记录数').value,'300 条')
        by_label(self.app.multiselect,'异常类型').set_value(['OVERDUE']).run()
        by_label(self.app.text_input,'订单号精确查询').set_value('SIM-1001').run()
        self.assertEqual(len(self.app.dataframe[0].value),2)
        by_label(self.app.selectbox,'处理状态').set_value('IN_PROGRESS')
        by_label(self.app.text_area,'处理动作或依据').set_value('页面测试：已联系仓库核对，待回复。')
        by_label(self.app.button,'保存记录与状态').click().run()
        self.assertEqual(len(self.app.exception),0)
        repo=Repository(self.db)
        batch=repo.list_batches()[0]['batch_id']
        logs=repo.actions(batch)
        self.assertEqual(len(logs),1)
        self.assertEqual(logs[0]['to_status'],'IN_PROGRESS')
        refreshed=AppTest.from_file(str(ROOT/'app.py'),default_timeout=20).run()
        by_label(refreshed.multiselect,'处理进展').set_value(['IN_PROGRESS']).run()
        self.assertEqual(len(refreshed.dataframe[0].value),1)
        self.assertTrue(any('页面测试' in x.value for x in refreshed.markdown))
        self.app.radio('page_nav').set_value('导入与校验').run()
        by_label(self.app.button,'检查数据').click().run()
        by_label(self.app.button,'保存批次并进入工作台').click().run()
        self.assertEqual(len(repo.list_batches()),1)
        self.assertEqual(len(repo.actions(batch)),1)
        self.assertEqual(len(self.app.exception),0)

    def test_actual_file_upload_and_structure_feedback(self):
        o,i=build_demo()
        self.app.radio('import_mode').set_value('上传模拟 CSV').run()
        self.app.file_uploader('order_upload').set_value(('bad.csv',b'order_id\n1\n','text/csv'))
        self.app.file_uploader('stock_upload').set_value(('模拟库存.csv',i,'text/csv')).run()
        by_label(self.app.button,'检查数据').click().run()
        self.assertTrue(any('缺少必需列' in x.value for x in self.app.error))
        self.app.file_uploader('order_upload').set_value(('模拟订单.csv',o,'text/csv')).run()
        self.save_demo()
        self.assertEqual(len(self.app.error),0)
        self.assertEqual(Repository(self.db).list_batches()[0]['order_file_name'],'模拟订单.csv')

    def test_stale_preview_cannot_save(self):
        by_label(self.app.button,'检查数据').click().run()
        by_label(self.app.text_input,'分析时点（北京时间）').set_value('2026-11-21 11:00:00').run()
        self.assertFalse(any(x.label=='保存批次并进入工作台' for x in self.app.button))
        self.assertTrue(any('重新点击' in x.value for x in self.app.info))

    def test_invalid_time_and_empty_note(self):
        by_label(self.app.text_input,'分析时点（北京时间）').set_value('不是日期').run()
        by_label(self.app.button,'检查数据').click().run()
        self.assertTrue(any('分析时点无法解析' in x.value for x in self.app.error))
        by_label(self.app.text_input,'分析时点（北京时间）').set_value('2026-11-21 10:00:00').run()
        self.save_demo()
        by_label(self.app.button,'保存记录与状态').click().run()
        self.assertTrue(any('处理内容不能为空' in x.value for x in self.app.error))
        repo=Repository(self.db)
        self.assertEqual(repo.actions(repo.list_batches()[0]['batch_id']),[])

    def test_inventory_unknown_and_zero_result(self):
        self.save_demo()
        self.app.radio('page_nav').set_value('库存供需汇总').run()
        self.assertEqual(len(self.app.dataframe[0].value),18)
        by_label(self.app.multiselect,'判断结果').set_value(['UNKNOWN_MISSING','UNKNOWN_INVALID']).run()
        self.assertEqual(len(self.app.dataframe[0].value),4)
        self.assertTrue(all(x=='无法判断' for x in self.app.dataframe[0].value['已知缺口（件）']))
        self.app.radio('page_nav').set_value('异常工作台').run()
        by_label(self.app.text_input,'订单号精确查询').set_value('DOES-NOT-EXIST').run()
        self.assertTrue(any('此范围没有异常项' in x.value for x in self.app.info))
        self.assertEqual(len(self.app.exception),0)


if __name__=='__main__':
    unittest.main()
