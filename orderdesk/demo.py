"""固定种子、固定时点的模拟业务数据；不含公司数据。"""
import csv
import io
import random
from datetime import datetime, timedelta
from pathlib import Path

from .constants import ORDER_FIELDS, INVENTORY_FIELDS, DEMO_TIME, TIME_FORMAT


def csv_bytes(rows, fields):
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode('utf-8-sig')


def build_demo():
    rng = random.Random(20261121)
    at = datetime.strptime(DEMO_TIME, TIME_FORMAT)
    rows = []
    for i in range(298):
        qty = rng.randint(2, 25)
        status = rng.choices(['ACTIVE', 'SHIPPED', 'CANCELLED'], weights=[65, 25, 10])[0]
        shipped = qty if status == 'SHIPPED' else (rng.randint(1, qty - 1) if rng.random() < .22 else 0)
        created = at - timedelta(days=12 - (i // 2) % 4)
        promised = at + timedelta(hours=rng.choice([-72, -24, -6, -1, 0, 6, 24, 48]))
        actual = (at - timedelta(hours=rng.randint(1, 48))).strftime(TIME_FORMAT) if shipped else ''
        rows.append(dict(zip(ORDER_FIELDS + ['actual_ship_at', 'case_label'],
                             [f'SIM-{1001 + i // 2}', str(10 * (i % 2 + 1)), created.strftime(TIME_FORMAT),
                              rng.choice(['WH-SH', 'WH-BJ', 'WH-SZ']), rng.choice(['SKU-A','SKU-B','SKU-C','SKU-D','SKU-E','SKU-F']),
                              qty, shipped, promised.strftime(TIME_FORMAT), status, actual,
                              '全部发货' if status == 'SHIPPED' else ('取消剩余履约' if status == 'CANCELLED' else '有效需求')])))
    # 固定的讲解入口，减少仅靠随机分布才能找到某类案例的情况。
    overrides = {
        0: dict(warehouse_id='WH-SH', sku_id='SKU-A', ordered_qty=10, shipped_qty=0, actual_ship_at='', line_status='ACTIVE', promised_ship_at='2026-11-21 09:00:00', case_label='超时未发货'),
        1: dict(warehouse_id='WH-SH', sku_id='SKU-A', ordered_qty=8, shipped_qty=3, actual_ship_at='2026-11-20 15:00:00', line_status='ACTIVE', promised_ship_at='2026-11-21 09:00:00', case_label='部分发货剩余超时'),
        2: dict(ordered_qty=5, shipped_qty=0, actual_ship_at='', line_status='ACTIVE', promised_ship_at=DEMO_TIME, case_label='恰好到点不超时'),
        3: dict(ordered_qty=5, shipped_qty=5, actual_ship_at='2026-11-20 15:00:00', line_status='SHIPPED', case_label='已发完排除'),
        4: dict(ordered_qty=5, shipped_qty=0, actual_ship_at='', line_status='CANCELLED', case_label='取消排除'),
        10: dict(sku_id='', case_label='商品编码缺失'),
        11: dict(promised_ship_at='', case_label='承诺时间缺失'),
        12: dict(promised_ship_at='2026-02-30 09:00:00', case_label='日期不存在'),
        13: dict(ordered_qty=-3, case_label='负数数量'),
        14: dict(ordered_qty='2.5', case_label='非整数数量'),
        15: dict(ordered_qty=2, shipped_qty=8, case_label='已发超过订购'),
        16: dict(order_created_at='2026-11-01 09:00:00', case_label='同订单头时间冲突'),
        18: dict(actual_ship_at='不是日期', case_label='实际发货日期错误'),
        19: dict(shipped_qty=0, actual_ship_at='2026-11-20 08:00:00', case_label='零已发量却有发货时间'),
        20: dict(order_id='', case_label='订单号缺失'),
    }
    for index, values in overrides.items():
        rows[index].update(values)
    for index in (22, 23):
        copy = dict(rows[index])
        copy['case_label'] = '重复订单行'
        rows.append(copy)
    inventory = []
    for wh in ['WH-SH', 'WH-BJ', 'WH-SZ']:
        for sku in ['SKU-A','SKU-B','SKU-C','SKU-D','SKU-E','SKU-F']:
            if (wh, sku) == ('WH-BJ', 'SKU-A'):
                continue
            qty = 2 if (wh,sku) == ('WH-SH','SKU-A') else (0 if (wh,sku) == ('WH-SH','SKU-B') else rng.randint(60, 400))
            inventory.append(dict(zip(INVENTORY_FIELDS, [wh, sku, qty, DEMO_TIME])))
    for row in inventory:
        if (row['warehouse_id'],row['sku_id']) == ('WH-SZ','SKU-C'):
            row['available_qty'] = -5
        if (row['warehouse_id'],row['sku_id']) == ('WH-SZ','SKU-D'):
            row['snapshot_at'] = '2026-11-20 10:00:00'
    inventory.append(dict(next(x for x in inventory if (x['warehouse_id'],x['sku_id']) == ('WH-BJ','SKU-B'))))
    return csv_bytes(rows, ORDER_FIELDS + ['actual_ship_at','case_label']), csv_bytes(inventory, INVENTORY_FIELDS)


def write_demo(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    orders, inventory = build_demo()
    (directory / '模拟数据_订单明细.csv').write_bytes(orders)
    (directory / '模拟数据_库存.csv').write_bytes(inventory)
    return orders, inventory
