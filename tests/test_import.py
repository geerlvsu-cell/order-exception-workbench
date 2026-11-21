import unittest
from orderdesk.constants import DEMO_TIME, ORDER_FIELDS
from orderdesk.demo import build_demo, csv_bytes
from orderdesk.import_service import parse_csv, ImportProblem
from orderdesk.validation import validate, analysis_time


def line(**changes):
    row = dict(zip(ORDER_FIELDS, ['SIM-O1','10','2026-11-20 09:00:00','WH-SH','SKU-A',6,0,'2026-11-21 09:00:00','ACTIVE']))
    row.update(changes)
    return row


def source(rows):
    return validate(parse_csv(csv_bytes(rows, list(rows[0])), 'ORDER'), DEMO_TIME)


class ImportTests(unittest.TestCase):
    def test_reproducible_300_rows(self):
        self.assertEqual(build_demo(), build_demo())
        orders, inventory = build_demo()
        result = validate(parse_csv(orders,'ORDER'),DEMO_TIME)
        self.assertEqual(len(result.rows),300)
        self.assertTrue(any(r['is_valid'] for r in result.rows))
        codes={e['code'] for r in result.rows for e in r['errors']}
        self.assertTrue({'FIELD_REQUIRED','DATETIME_FORMAT_INVALID','DUPLICATE_LINE_KEY','ORDER_HEADER_CONFLICT'} <= codes)
        self.assertEqual(len(parse_csv(inventory,'INVENTORY').rows),18)

    def test_structure(self):
        for content in (b'', b'order_id,order_id\na,b\n', b'order_id\n1\n', csv_bytes([], ORDER_FIELDS), b'\xff\xfe'):
            with self.subTest(content=content), self.assertRaises(ImportProblem):
                parse_csv(content,'ORDER')
        with self.assertRaises(ImportProblem):
            parse_csv(csv_bytes([line()],ORDER_FIELDS)+b'one,two\n','ORDER')

    def test_invalid_dates_and_missing_fields_are_reported(self):
        result=source([line(promised_ship_at='2026-02-30 10:00:00',sku_id='')])
        self.assertEqual({e['code'] for e in result.rows[0]['errors']},{'FIELD_REQUIRED','DATETIME_FORMAT_INVALID'})
        self.assertFalse(result.rows[0]['is_valid'])

    def test_duplicate_all_quarantined(self):
        rows=source([line(),line()]).rows
        self.assertTrue(all(not r['is_valid'] for r in rows))
        self.assertTrue(all(any(e['code']=='DUPLICATE_LINE_KEY' for e in r['errors']) for r in rows))

    def test_header_conflict(self):
        rows=source([line(),line(line_id='20',order_created_at='2026-11-20 08:00:00')]).rows
        self.assertTrue(all(not r['is_valid'] for r in rows))

    def test_identifier_preservation_and_zero(self):
        row=source([line(order_id=' 0001 ',line_id='001',shipped_qty='0')]).rows[0]
        self.assertEqual(row['values']['order_id'],'0001')
        self.assertEqual(row['values']['line_id'],'001')
        self.assertEqual(row['raw_values']['order_id'],' 0001 ')
        self.assertTrue(row['is_valid'])

    def test_quantity_and_ship_state_consistency(self):
        for change in ({'ordered_qty':'1.0'},{'ordered_qty':-1},{'shipped_qty':7},
                       {'line_status':'SHIPPED'},{'actual_ship_at':'2026-11-20 12:00:00'},
                       {'shipped_qty':2,'actual_ship_at':''}):
            with self.subTest(change=change):
                self.assertFalse(source([line(**change)]).rows[0]['is_valid'])
        self.assertTrue(source([line(shipped_qty=6,line_status='SHIPPED',actual_ship_at='2026-11-20 12:00:00')]).rows[0]['is_valid'])

    def test_analysis_and_source_time_validation(self):
        with self.assertRaises(ImportProblem):
            analysis_time('2026-2-30')
        self.assertFalse(source([line(order_created_at='2026-11-22 00:00:00')]).rows[0]['is_valid'])
        self.assertFalse(source([line(promised_ship_at='2026-11-01 00:00:00')]).rows[0]['is_valid'])


if __name__ == '__main__':
    unittest.main()
