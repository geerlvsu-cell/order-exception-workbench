import re
from collections import defaultdict
from datetime import datetime

from .constants import LABELS, ORDER_FIELDS, INVENTORY_FIELDS, TIME_FORMAT
from .import_service import ImportProblem

ID_RE = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')
INT_RE = re.compile(r'-?[0-9]+\Z')
DATE_RE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}\Z')


def valid_id(value):
    return isinstance(value, str) and bool(ID_RE.fullmatch(value))


def parse_time(value):
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        raise ValueError('日期格式不符合要求')
    return datetime.strptime(value, TIME_FORMAT)


def analysis_time(value):
    try:
        parse_time(value)
        return value
    except (ValueError, TypeError) as exc:
        raise ImportProblem('分析时点无法解析。请填写真实存在的北京时间，格式如 2026-11-21 10:00:00。') from exc


def add_error(row, code, field, message, related=None):
    error = {'code': code, 'field': field, 'raw_value': row['raw_values'].get(field), 'message': message}
    if related:
        error['related_source_rows'] = related
    if error not in row['errors']:
        row['errors'].append(error)


def validate(source, at):
    analysis_time(at)
    order = source.source_type == 'ORDER'
    required = ORDER_FIELDS if order else INVENTORY_FIELDS
    for row in source.rows:
        values = row['values']
        for field in required:
            if not values.get(field):
                add_error(row, 'FIELD_REQUIRED', field, f'{LABELS[field]}为空。请补齐来源表中的 {field}。')
        for field in (['order_id', 'line_id', 'warehouse_id', 'sku_id'] if order else ['warehouse_id', 'sku_id']):
            if values.get(field) and not valid_id(values[field]):
                add_error(row, 'ID_FORMAT_INVALID', field, f'{LABELS[field]}格式不合法。请使用 1–64 位字母、数字、下划线或短横线，保留前导零。')
        for field in (['ordered_qty', 'shipped_qty'] if order else ['available_qty']):
            value = values.get(field)
            if not value:
                continue
            if not INT_RE.fullmatch(value) or len(value.lstrip('-')) > 10:
                add_error(row, 'QTY_FORMAT_INVALID', field, f'{LABELS[field]}必须为十进制整数。请删除小数点、逗号或其他符号。')
                continue
            values[field] = int(value)
            low = 1 if field == 'ordered_qty' else 0
            if not low <= values[field] <= 9999999:
                add_error(row, 'QTY_OUT_OF_RANGE', field, f'{LABELS[field]}应在 {low} 至 9,999,999 之间，请核对数量。')
        dates = ['order_created_at', 'promised_ship_at', 'actual_ship_at'] if order else ['snapshot_at']
        good_dates = set()
        for field in dates:
            if values.get(field):
                try:
                    parse_time(values[field])
                    good_dates.add(field)
                except ValueError:
                    add_error(row, 'DATETIME_FORMAT_INVALID', field, f'{LABELS[field]}不是合法日期。请按 YYYY-MM-DD HH:MM:SS 填写真实时间。')
        if order:
            if values.get('line_status') and values['line_status'] not in ('ACTIVE', 'CANCELLED', 'SHIPPED'):
                add_error(row, 'LINE_STATUS_INVALID', 'line_status', '订单状态仅接受 ACTIVE、CANCELLED、SHIPPED，请按模板修正。')
            qty, shipped = values.get('ordered_qty'), values.get('shipped_qty')
            if isinstance(qty, int) and isinstance(shipped, int):
                if shipped > qty:
                    add_error(row, 'SHIPPED_EXCEEDS_ORDERED', 'shipped_qty', '累计已发数量超过订购数量，请核对来源记录。')
                if values.get('line_status') == 'SHIPPED' and shipped != qty:
                    add_error(row, 'SHIPPED_STATUS_MISMATCH', 'line_status', 'SHIPPED 表示全部发完，但已发量不等于订购量。请修正数量或改为 ACTIVE。')
            if 'order_created_at' in good_dates and values['order_created_at'] > at:
                add_error(row, 'ORDER_CREATED_AFTER_ANALYSIS', 'order_created_at', '下单时间晚于分析时点，请核对订单快照或调整分析时点。')
            if {'order_created_at', 'promised_ship_at'} <= good_dates and values['promised_ship_at'] < values['order_created_at']:
                add_error(row, 'PROMISE_BEFORE_ORDER', 'promised_ship_at', '承诺发货时间早于下单时间，请核对来源时间。')
            # 兼容上一轮九列模板；一旦提供此列，就校验它与发货量的一致性。
            if 'actual_ship_at' in values and isinstance(shipped, int):
                if shipped > 0 and not values['actual_ship_at']:
                    add_error(row, 'FIELD_REQUIRED', 'actual_ship_at', '已有发货数量但实际发货时间为空，请补齐最近一次实际发货时间。')
                if shipped == 0 and values['actual_ship_at']:
                    add_error(row, 'SHIP_TIME_WITH_ZERO_QTY', 'actual_ship_at', '已发量为零却填写了发货时间，请核对时间或数量。')
            if 'actual_ship_at' in good_dates:
                if values['actual_ship_at'] > at:
                    add_error(row, 'SHIP_TIME_AFTER_ANALYSIS', 'actual_ship_at', '实际发货时间晚于分析时点，请核对同一时点的订单快照。')
                if 'order_created_at' in good_dates and values['actual_ship_at'] < values['order_created_at']:
                    add_error(row, 'SHIP_TIME_BEFORE_ORDER', 'actual_ship_at', '实际发货时间早于下单时间，请核对来源时间。')
        elif 'snapshot_at' in good_dates and values['snapshot_at'] != at:
            add_error(row, 'STOCK_SNAPSHOT_MISMATCH', 'snapshot_at', '库存快照时间不等于分析时点，请使用同一时点的订单和库存快照。')

    keys = ['order_id', 'line_id'] if order else ['warehouse_id', 'sku_id']
    groups = defaultdict(list)
    heads = defaultdict(list)
    for row in source.rows:
        values = row['values']
        if all(valid_id(values.get(k)) for k in keys):
            groups[tuple(values[k] for k in keys)].append(row)
        if order and valid_id(values.get('order_id')):
            heads[values['order_id']].append(row)
    for group in groups.values():
        if len(group) > 1:
            related = [r['source_row_no'] for r in group]
            for row in group:
                add_error(row, 'DUPLICATE_LINE_KEY' if order else 'DUPLICATE_STOCK_KEY', '+'.join(keys),
                          f'业务键重复，涉及来源记录 {related}。请在源表核对并保留唯一记录，再作为新批次导入；本次全部排除。', related)
    for group in heads.values():
        times = set()
        for row in group:
            try:
                parse_time(row['values']['order_created_at'])
                times.add(row['values']['order_created_at'])
            except ValueError:
                pass
        if len(times) > 1:
            related = [r['source_row_no'] for r in group]
            for row in group:
                add_error(row, 'ORDER_HEADER_CONFLICT', 'order_created_at',
                          f'同一订单的下单时间不一致，涉及记录 {related}。请统一真实下单时间后重新导入；本次整单排除。', related)
    for row in source.rows:
        row['is_valid'] = not row['errors']
    return source
