import unittest
from orderdesk.constants import DEMO_TIME, INVENTORY_FIELDS
from orderdesk.demo import csv_bytes, build_demo
from orderdesk.rules import analyze, filter_exceptions
from tests.test_import import line


def stock(warehouse_id='WH-SH',sku_id='SKU-A',available_qty=8,**changes):
    row=dict(warehouse_id=warehouse_id,sku_id=sku_id,available_qty=available_qty,snapshot_at=DEMO_TIME)
    row.update(changes)
    return row


def calculate(rows, inventory=None, at=DEMO_TIME):
    return analyze(csv_bytes(rows,list(rows[0])),csv_bytes(inventory or [stock()],INVENTORY_FIELDS),at)


class RuleTests(unittest.TestCase):
    def test_time_boundary_and_partial(self):
        for promised,shipped,expected in [('2026-11-21 10:00:01',0,0),(DEMO_TIME,0,0),('2026-11-21 09:59:59',0,1),('2026-11-21 09:59:59',2,1),('2026-11-21 09:59:59',6,0)]:
            with self.subTest(promised=promised,shipped=shipped):
                a=calculate([line(promised_ship_at=promised,shipped_qty=shipped)])
                self.assertEqual(a.metrics['overdue_line_count'],expected)
        self.assertEqual(calculate([line(line_status='CANCELLED')]).inventory_checks,[])
        self.assertEqual(calculate([line(line_status='SHIPPED',shipped_qty=6)]).inventory_checks,[])

    def test_aggregate_before_stock_join_and_grain(self):
        a=calculate([line(ordered_qty=6),line(line_id='20',ordered_qty=4)])
        self.assertEqual(a.metrics['overdue_order_count'],1)
        self.assertEqual(a.metrics['overdue_line_count'],2)
        self.assertEqual(a.metrics['stock_gap_group_count'],1)
        self.assertEqual(a.inventory_checks[0]['demand_qty'],10)
        self.assertEqual(a.inventory_checks[0]['gap_qty'],2)
        risk=next(e for e in a.exceptions if e['exception_type']=='STOCK_GAP')
        self.assertIsNone(risk['order_id'])

    def test_cross_warehouse_no_netting(self):
        a=calculate([line(warehouse_id='WH-BJ')],[stock(available_qty=100),stock('WH-BJ',available_qty=1)])
        self.assertEqual(a.inventory_checks[0]['gap_qty'],5)

    def test_stock_equal_zero_missing_invalid_and_duplicate(self):
        for inventory,status,gap in [([stock(available_qty=6)],'NO_GAP_IN_VALID_DATA',0),
                                     ([stock(available_qty=0)],'GAP',6),
                                     ([stock(sku_id='SKU-B')],'UNKNOWN_MISSING',None),
                                     ([stock(available_qty=-1)],'UNKNOWN_INVALID',None),
                                     ([stock(),stock()],'UNKNOWN_INVALID',None),
                                     ([stock(snapshot_at='2026-11-20 00:00:00')],'UNKNOWN_INVALID',None)]:
            with self.subTest(status=status,inventory=inventory):
                a=calculate([line()],inventory)
                self.assertEqual(a.inventory_checks[0]['supply_status'],status)
                self.assertEqual(a.inventory_checks[0]['gap_qty'],gap)
                missing=[e for e in a.exceptions if 'MISSING_INVENTORY' in e['reason_codes']]
                self.assertEqual(len(missing),1 if status=='UNKNOWN_MISSING' else 0)

    def test_bad_line_not_demand_and_one_quality_item(self):
        a=calculate([line(promised_ship_at='',ordered_qty='x')])
        self.assertEqual(a.metrics['data_quality_issue_count'],1)
        self.assertEqual(a.metrics['valid_order_line_count'],0)
        self.assertEqual(a.metrics['overdue_line_count'],0)
        self.assertEqual(a.inventory_checks,[])
        self.assertEqual(len(a.exceptions[0]['reason_codes']),2)

    def test_document_example(self):
        rows=[line(order_id='SIM-O1001'),line(order_id='SIM-O1001',line_id='20',ordered_qty=4),
              line(order_id='SIM-O1002',sku_id='SKU-B',ordered_qty=5,shipped_qty=2,promised_ship_at=DEMO_TIME),
              line(order_id='SIM-O1003',warehouse_id='WH-BJ',ordered_qty=2,promised_ship_at='2026-11-21 12:00:00'),
              line(order_id='SIM-O1004',ordered_qty=3,line_status='CANCELLED'),
              line(order_id='SIM-O1005',ordered_qty=2,shipped_qty=2),
              line(order_id='SIM-O1006',ordered_qty=2,promised_ship_at='')]
        a=calculate(rows,[stock(),stock(sku_id='SKU-B',available_qty=3)])
        m=a.metrics
        self.assertEqual([m[x] for x in ['input_order_count','input_order_row_count','valid_order_line_count','overdue_order_count','overdue_line_count','stock_gap_group_count','unknown_stock_group_count','data_quality_issue_count','open_exception_count']], [6,7,6,1,2,1,1,2,5])

    def test_repeat_and_changed_snapshot(self):
        orders,inventory=build_demo()
        a=analyze(orders,inventory,DEMO_TIME)
        b=analyze(orders,inventory,DEMO_TIME,'renamed.csv')
        self.assertEqual(a.batch['batch_id'],b.batch['batch_id'])
        self.assertEqual([e['exception_id'] for e in a.exceptions],[e['exception_id'] for e in b.exceptions])
        self.assertNotEqual(a.batch['batch_id'],analyze(orders,inventory,'2026-11-21 11:00:00').batch['batch_id'])

    def test_filters_and_missing_identifier_option(self):
        a=calculate([line(),line(order_id='SIM-O2',line_id='20',warehouse_id='')])
        matched=filter_exceptions(a.exceptions,types=['DATA_QUALITY'],warehouses=[None])
        self.assertEqual(len(matched),1)
        self.assertEqual(len(filter_exceptions(a.exceptions,types=['OVERDUE'],order_id='SIM-O1')),1)


if __name__=='__main__':
    unittest.main()
