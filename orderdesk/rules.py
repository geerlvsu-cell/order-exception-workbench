import hashlib
import json
import uuid
from collections import defaultdict
from dataclasses import dataclass

from .constants import RULE_VERSION, TIMEZONE
from .import_service import parse_csv
from .validation import validate, valid_id, analysis_time, parse_time


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


@dataclass
class Analysis:
    batch: dict
    raw_rows: list
    inventory_checks: list
    exceptions: list
    metrics: dict
    extra_columns: dict


def analyze(order_bytes, inventory_bytes, at, order_name='模拟数据_订单明细.csv', inventory_name='模拟数据_库存.csv'):
    at = analysis_time(at)
    orders = validate(parse_csv(order_bytes, 'ORDER'), at)
    stock = validate(parse_csv(inventory_bytes, 'INVENTORY'), at)
    fingerprint = hashlib.sha256(json_text([orders.file_hash, stock.file_hash, at, RULE_VERSION, 'SIMULATED']).encode()).hexdigest()
    batch_id = str(uuid.uuid5(uuid.NAMESPACE_URL, fingerprint))
    batch = dict(batch_id=batch_id, batch_fingerprint=fingerprint, data_mode='SIMULATED',
                 analysis_at=at, timezone=TIMEZONE, rule_version=RULE_VERSION,
                 order_file_name=order_name, inventory_file_name=inventory_name,
                 order_file_hash=orders.file_hash, inventory_file_hash=stock.file_hash,
                 order_row_count=len(orders.rows), inventory_row_count=len(stock.rows),
                 ignored_blank_order_rows=orders.ignored_blank_rows, ignored_blank_inventory_rows=stock.ignored_blank_rows)
    raw_rows = orders.rows + stock.rows
    for row in raw_rows:
        row['raw_row_id'] = str(uuid.uuid5(uuid.UUID(batch_id), f"{row['source_type']}:{row['source_row_no']}"))
        row['batch_id'] = batch_id
    exceptions = []

    def incident(kind, target, target_key, values, evidence, reasons, summary, raw=None):
        key = hashlib.sha256(json_text([batch_id, kind, target, target_key]).encode()).hexdigest()
        item = dict(exception_id=str(uuid.uuid5(uuid.UUID(batch_id), key)), batch_id=batch_id,
                    exception_key=key, exception_type=kind, target_kind=target,
                    raw_row_id=raw['raw_row_id'] if raw else None,
                    reason_codes=reasons, trigger_summary=summary, evidence=evidence,
                    processing_status='TODO', last_action_at=None, revision=0)
        for field in ['order_id', 'line_id', 'warehouse_id', 'sku_id']:
            item[field] = values.get(field) if valid_id(values.get(field)) else None
        exceptions.append(item)

    for row in raw_rows:
        if row['errors']:
            evidence = dict(source_type=row['source_type'], source_row_no=row['source_row_no'],
                            raw_values=row['raw_values'], errors=row['errors'])
            incident('DATA_QUALITY', 'ORDER_ROW' if row['source_type']=='ORDER' else 'INVENTORY_ROW',
                     row['raw_row_id'], row['values'], evidence,
                     list(dict.fromkeys(e['code'] for e in row['errors'])),
                     '；'.join(e['message'] for e in row['errors']), row)

    demand = defaultdict(list)
    for row in orders.rows:
        if not row['is_valid']:
            continue
        value = row['values']
        remaining = value['ordered_qty'] - value['shipped_qty']
        if value['line_status'] != 'ACTIVE' or remaining <= 0:
            continue
        evidence = dict(order_id=value['order_id'], line_id=value['line_id'],
                        source_row_no=row['source_row_no'], remaining_qty=remaining)
        demand[(value['warehouse_id'], value['sku_id'])].append(evidence)
        if at > value['promised_ship_at']:
            seconds = int((parse_time(at) - parse_time(value['promised_ship_at'])).total_seconds())
            evidence = dict(evidence, analysis_at=at, promised_ship_at=value['promised_ship_at'],
                            ordered_qty=value['ordered_qty'], shipped_qty=value['shipped_qty'],
                            actual_ship_at=value.get('actual_ship_at'), overdue_seconds=seconds,
                            formula='分析时点 > 承诺发货时间，且未取消、尚有未发数量')
            incident('OVERDUE', 'ORDER_LINE', [value['order_id'], value['line_id']], value, evidence,
                     ['OVERDUE_REMAINING'], f"尚有 {remaining} 件未发，超过承诺时间 {seconds / 3600:g} 小时。", row)

    stock_groups = defaultdict(list)
    for row in stock.rows:
        value = row['values']
        if valid_id(value.get('warehouse_id')) and valid_id(value.get('sku_id')):
            stock_groups[(value['warehouse_id'], value['sku_id'])].append(row)
    checks = []
    for (warehouse, sku), lines in sorted(demand.items()):
        qty = sum(x['remaining_qty'] for x in lines)
        sources = stock_groups[(warehouse, sku)]
        inventory, gap = None, None
        if not sources:
            status = 'UNKNOWN_MISSING'
        elif len(sources) != 1 or not sources[0]['is_valid']:
            status = 'UNKNOWN_INVALID'
        else:
            inventory = sources[0]['values']['available_qty']
            gap = max(qty - inventory, 0)
            status = 'GAP' if gap > 0 else 'NO_GAP_IN_VALID_DATA'
        evidence = dict(analysis_at=at, demand_lines=lines, demand_qty=qty,
                        inventory_sources=[dict(source_row_no=x['source_row_no'], raw_values=x['raw_values'], errors=x['errors']) for x in sources],
                        available_qty=inventory, gap_qty=gap, demand_scope='VALID_ORDER_LINES_ONLY',
                        formula='缺口 = max(有效未发需求合计 - 对本批可用库存, 0)；库存未知时不计算数值缺口')
        check = dict(batch_id=batch_id, warehouse_id=warehouse, sku_id=sku, demand_qty=qty,
                     available_qty=inventory, gap_qty=gap, supply_status=status, demand_line_count=len(lines),
                     demand_order_count=len({x['order_id'] for x in lines}), evidence=evidence)
        checks.append(check)
        target = dict(warehouse_id=warehouse, sku_id=sku)
        if status == 'UNKNOWN_MISSING':
            incident('DATA_QUALITY','STOCK_GROUP',[warehouse, sku],target,evidence,['MISSING_INVENTORY'],
                     f'{warehouse} / {sku} 有 {qty} 件有效未发需求，但库存表无匹配记录。请补齐同一时点库存后重新导入。')
        if status == 'GAP':
            incident('STOCK_GAP','STOCK_GROUP',[warehouse, sku],target,evidence,['DEMAND_EXCEEDS_AVAILABLE'],
                     f'有效需求 {qty} 件，可用库存 {inventory} 件，已知缺口 {gap} 件；未分配到具体订单。')

    metrics = dict(input_order_count=len({r['values']['order_id'] for r in orders.rows if valid_id(r['values'].get('order_id'))}),
                   input_order_row_count=len(orders.rows), valid_order_line_count=sum(r['is_valid'] for r in orders.rows),
                   invalid_order_row_count=sum(not r['is_valid'] for r in orders.rows),
                   invalid_inventory_row_count=sum(not r['is_valid'] for r in stock.rows),
                   unidentifiable_order_row_count=sum(not valid_id(r['values'].get('order_id')) for r in orders.rows),
                   overdue_order_count=len({e['order_id'] for e in exceptions if e['exception_type']=='OVERDUE'}),
                   overdue_line_count=sum(e['exception_type']=='OVERDUE' for e in exceptions),
                   stock_gap_group_count=sum(c['supply_status']=='GAP' for c in checks),
                   unknown_stock_group_count=sum(c['supply_status'].startswith('UNKNOWN') for c in checks),
                   data_quality_issue_count=sum(e['exception_type']=='DATA_QUALITY' for e in exceptions),
                   open_exception_count=len(exceptions))
    return Analysis(batch,raw_rows,checks,exceptions,metrics,{'ORDER':orders.extra_columns,'INVENTORY':stock.extra_columns})


def filter_exceptions(items, types=(), statuses=(), warehouses=(), skus=(), order_id=''):
    result=[]
    for item in items:
        if types and item['exception_type'] not in types:
            continue
        if statuses and item['processing_status'] not in statuses:
            continue
        if warehouses and item['warehouse_id'] not in warehouses:
            continue
        if skus and item['sku_id'] not in skus:
            continue
        if order_id.strip() and item['order_id'] != order_id.strip():
            continue
        result.append(item)
    rank={'TODO':0,'IN_PROGRESS':1,'RESOLVED':2,'IGNORED':3}
    return sorted(result,key=lambda x:(rank[x['processing_status']],x['exception_type'],x['order_id'] or '',x['line_id'] or '',x['warehouse_id'] or '',x['sku_id'] or '',x['exception_id']))
