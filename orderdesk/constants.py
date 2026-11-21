RULE_VERSION = 'v1.1'
TIME_FORMAT = '%Y-%m-%d %H:%M:%S'
TIMEZONE = 'Asia/Shanghai'
DEMO_TIME = '2026-11-21 10:00:00'
ORDER_FIELDS = ['order_id', 'line_id', 'order_created_at', 'warehouse_id', 'sku_id',
                'ordered_qty', 'shipped_qty', 'promised_ship_at', 'line_status']
INVENTORY_FIELDS = ['warehouse_id', 'sku_id', 'available_qty', 'snapshot_at']
LABELS = dict(zip(ORDER_FIELDS, ['订单号', '订单行号', '下单时间', '仓库', '商品编码',
                               '订购数量', '累计已发数量', '承诺发货时间', '订单状态']))
LABELS.update(actual_ship_at='最近实际发货时间', available_qty='对本批可用库存', snapshot_at='库存快照时间')
TYPE_LABELS = {'OVERDUE': '超时未发货', 'DATA_QUALITY': '数据质量问题', 'STOCK_GAP': '库存供需缺口'}
STATUS_LABELS = {'TODO': '待处理', 'IN_PROGRESS': '处理中', 'RESOLVED': '已解决', 'IGNORED': '已忽略'}
SUPPLY_LABELS = {'GAP': '存在已知需求缺口', 'NO_GAP_IN_VALID_DATA': '有效数据范围内未发现缺口',
                 'UNKNOWN_MISSING': '库存记录缺失，无法判断', 'UNKNOWN_INVALID': '库存记录不合法，无法判断'}
TRANSITIONS = {'TODO': ['TODO', 'IN_PROGRESS', 'RESOLVED', 'IGNORED'],
               'IN_PROGRESS': ['IN_PROGRESS', 'RESOLVED', 'IGNORED'],
               'RESOLVED': ['RESOLVED', 'IN_PROGRESS'], 'IGNORED': ['IGNORED', 'IN_PROGRESS']}
