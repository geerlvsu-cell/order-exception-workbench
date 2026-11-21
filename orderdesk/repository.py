import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .constants import TIMEZONE, TIME_FORMAT, TRANSITIONS
from .rules import json_text


def now():
    return datetime.now(ZoneInfo(TIMEZONE)).strftime(TIME_FORMAT)


class StateConflict(ValueError):
    pass


SCHEMA = '''
CREATE TABLE IF NOT EXISTS batches (
 batch_id TEXT PRIMARY KEY, batch_fingerprint TEXT NOT NULL UNIQUE,
 analysis_at TEXT NOT NULL, created_at TEXT NOT NULL, metadata_json TEXT NOT NULL,
 metrics_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS raw_rows (
 raw_row_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(batch_id),
 source_type TEXT NOT NULL CHECK(source_type IN ('ORDER','INVENTORY')),
 source_row_no INTEGER NOT NULL, raw_values_json TEXT NOT NULL,
 normalized_values_json TEXT NOT NULL, is_valid INTEGER NOT NULL CHECK(is_valid IN (0,1)),
 validation_errors_json TEXT NOT NULL, UNIQUE(batch_id,source_type,source_row_no)
);
CREATE TABLE IF NOT EXISTS orders (
 batch_id TEXT NOT NULL REFERENCES batches(batch_id), order_id TEXT NOT NULL,
 order_created_at TEXT NOT NULL, PRIMARY KEY(batch_id,order_id)
);
CREATE TABLE IF NOT EXISTS order_lines (
 batch_id TEXT NOT NULL, order_id TEXT NOT NULL, line_id TEXT NOT NULL,
 raw_row_id TEXT NOT NULL UNIQUE REFERENCES raw_rows(raw_row_id),
 warehouse_id TEXT NOT NULL, sku_id TEXT NOT NULL,
 ordered_qty INTEGER NOT NULL CHECK(ordered_qty>0),
 shipped_qty INTEGER NOT NULL CHECK(shipped_qty>=0 AND shipped_qty<=ordered_qty),
 promised_ship_at TEXT NOT NULL, actual_ship_at TEXT,
 line_status TEXT NOT NULL CHECK(line_status IN ('ACTIVE','CANCELLED','SHIPPED')),
 PRIMARY KEY(batch_id,order_id,line_id), FOREIGN KEY(batch_id,order_id) REFERENCES orders(batch_id,order_id)
);
CREATE TABLE IF NOT EXISTS inventory_snapshots (
 batch_id TEXT NOT NULL REFERENCES batches(batch_id), warehouse_id TEXT NOT NULL,
 sku_id TEXT NOT NULL, raw_row_id TEXT NOT NULL UNIQUE REFERENCES raw_rows(raw_row_id),
 available_qty INTEGER NOT NULL CHECK(available_qty>=0), snapshot_at TEXT NOT NULL,
 PRIMARY KEY(batch_id,warehouse_id,sku_id)
);
CREATE TABLE IF NOT EXISTS inventory_checks (
 batch_id TEXT NOT NULL REFERENCES batches(batch_id), warehouse_id TEXT NOT NULL, sku_id TEXT NOT NULL,
 demand_qty INTEGER NOT NULL CHECK(demand_qty>0), available_qty INTEGER, gap_qty INTEGER,
 supply_status TEXT NOT NULL, demand_line_count INTEGER NOT NULL, demand_order_count INTEGER NOT NULL,
 evidence_json TEXT NOT NULL, PRIMARY KEY(batch_id,warehouse_id,sku_id),
 CHECK((supply_status IN ('GAP','NO_GAP_IN_VALID_DATA') AND available_qty IS NOT NULL AND gap_qty IS NOT NULL)
 OR (supply_status IN ('UNKNOWN_MISSING','UNKNOWN_INVALID') AND available_qty IS NULL AND gap_qty IS NULL))
);
CREATE TABLE IF NOT EXISTS exceptions (
 exception_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(batch_id),
 exception_key TEXT NOT NULL UNIQUE, exception_type TEXT NOT NULL,
 target_kind TEXT NOT NULL, raw_row_id TEXT REFERENCES raw_rows(raw_row_id),
 order_id TEXT, line_id TEXT, warehouse_id TEXT, sku_id TEXT,
 reason_codes_json TEXT NOT NULL, trigger_summary TEXT NOT NULL, evidence_json TEXT NOT NULL,
 processing_status TEXT NOT NULL CHECK(processing_status IN ('TODO','IN_PROGRESS','RESOLVED','IGNORED')),
 detected_at TEXT NOT NULL, last_action_at TEXT, revision INTEGER NOT NULL DEFAULT 0,
 CHECK((target_kind IN ('ORDER_LINE','ORDER_ROW','INVENTORY_ROW') AND raw_row_id IS NOT NULL)
 OR (target_kind='STOCK_GROUP' AND warehouse_id IS NOT NULL AND sku_id IS NOT NULL AND order_id IS NULL AND line_id IS NULL))
);
CREATE INDEX IF NOT EXISTS exceptions_by_batch ON exceptions(batch_id,processing_status,exception_type);
CREATE TABLE IF NOT EXISTS action_logs (
 action_id INTEGER PRIMARY KEY AUTOINCREMENT, exception_id TEXT NOT NULL REFERENCES exceptions(exception_id),
 action_text TEXT NOT NULL CHECK(length(trim(action_text)) BETWEEN 1 AND 1000),
 from_status TEXT NOT NULL, to_status TEXT NOT NULL, actor_display_name TEXT NOT NULL,
 acted_at TEXT NOT NULL
);
PRAGMA user_version=1;
'''


def insert(db, table, row):
    # 表名和字段名均由本模块常量构造；用户输入只作为绑定参数传递。
    fields = ','.join(row)
    db.execute(f'INSERT INTO {table} ({fields}) VALUES ({",".join("?" for _ in row)})', list(row.values()))


class Repository:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connection(self):
        db=sqlite3.connect(self.path,timeout=10)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def save_analysis(self, analysis):
        batch=analysis.batch
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            existing=db.execute('SELECT batch_id FROM batches WHERE batch_fingerprint=?',(batch['batch_fingerprint'],)).fetchone()
            if existing:
                return existing['batch_id'],False
            timestamp=now()
            insert(db,'batches',dict(batch_id=batch['batch_id'],batch_fingerprint=batch['batch_fingerprint'],
                   analysis_at=batch['analysis_at'],created_at=timestamp,metadata_json=json_text(batch),metrics_json=json_text(analysis.metrics)))
            for r in analysis.raw_rows:
                insert(db,'raw_rows',dict(raw_row_id=r['raw_row_id'],batch_id=batch['batch_id'],source_type=r['source_type'],
                       source_row_no=r['source_row_no'],raw_values_json=json_text(r['raw_values']),normalized_values_json=json_text(r['values']),
                       is_valid=int(r['is_valid']),validation_errors_json=json_text(r['errors'])))
            heads=set()
            for r in analysis.raw_rows:
                if not r['is_valid']:
                    continue
                v=r['values']
                if r['source_type']=='ORDER':
                    if v['order_id'] not in heads:
                        insert(db,'orders',dict(batch_id=batch['batch_id'],order_id=v['order_id'],order_created_at=v['order_created_at']))
                        heads.add(v['order_id'])
                    row={k:v[k] for k in ['order_id','line_id','warehouse_id','sku_id','ordered_qty','shipped_qty','promised_ship_at','line_status']}
                    row.update(batch_id=batch['batch_id'],raw_row_id=r['raw_row_id'],actual_ship_at=v.get('actual_ship_at') or None)
                    insert(db,'order_lines',row)
                else:
                    row={k:v[k] for k in ['warehouse_id','sku_id','available_qty','snapshot_at']}
                    row.update(batch_id=batch['batch_id'],raw_row_id=r['raw_row_id'])
                    insert(db,'inventory_snapshots',row)
            for c in analysis.inventory_checks:
                row={k:v for k,v in c.items() if k!='evidence'}
                row['evidence_json']=json_text(c['evidence'])
                insert(db,'inventory_checks',row)
            for e in analysis.exceptions:
                row={k:v for k,v in e.items() if k not in ('evidence','reason_codes')}
                row.update(evidence_json=json_text(e['evidence']),reason_codes_json=json_text(e['reason_codes']),detected_at=timestamp)
                insert(db,'exceptions',row)
        return batch['batch_id'],True

    def list_batches(self):
        with self.connection() as db:
            return [dict(json.loads(r['metadata_json']),created_at=r['created_at']) for r in db.execute('SELECT * FROM batches ORDER BY created_at DESC,rowid DESC')]

    def batch(self,batch_id):
        with self.connection() as db:
            r=db.execute('SELECT * FROM batches WHERE batch_id=?',(batch_id,)).fetchone()
            if not r:
                raise ValueError('找不到该批次，请重新选择已保存的批次。')
            return dict(json.loads(r['metadata_json']),created_at=r['created_at'])

    def metrics(self,batch_id):
        with self.connection() as db:
            row=db.execute('SELECT metrics_json FROM batches WHERE batch_id=?',(batch_id,)).fetchone()
            m=json.loads(row['metrics_json'])
            m['open_exception_count']=db.execute("SELECT count(*) FROM exceptions WHERE batch_id=? AND processing_status IN ('TODO','IN_PROGRESS')",(batch_id,)).fetchone()[0]
            return m

    def exceptions(self,batch_id):
        with self.connection() as db:
            result=[]
            for row in db.execute('''SELECT e.*,r.source_type,r.source_row_no FROM exceptions e
                                    LEFT JOIN raw_rows r ON e.raw_row_id=r.raw_row_id WHERE e.batch_id=?''',(batch_id,)):
                item=dict(row)
                item['evidence']=json.loads(item.pop('evidence_json'))
                item['reason_codes']=json.loads(item.pop('reason_codes_json'))
                result.append(item)
            return result

    def inventory_checks(self,batch_id):
        with self.connection() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM inventory_checks WHERE batch_id=? ORDER BY warehouse_id,sku_id',(batch_id,))]
            for r in rows:
                r['evidence']=json.loads(r.pop('evidence_json'))
            return rows

    def actions(self,batch_id,exception_ids=None):
        with self.connection() as db:
            rows=[dict(r) for r in db.execute('''SELECT a.*,e.exception_type,e.target_kind,e.order_id,e.line_id,e.warehouse_id,e.sku_id
                   FROM action_logs a JOIN exceptions e ON a.exception_id=e.exception_id
                   WHERE e.batch_id=? ORDER BY a.acted_at,a.action_id''',(batch_id,))]
        ids=set(exception_ids) if exception_ids is not None else None
        return rows if ids is None else [r for r in rows if r['exception_id'] in ids]

    def add_action(self,exception_id,to_status,text,actor,expected_revision):
        text=text.strip()
        actor=actor.strip() or '本地演示用户'
        if not 1<=len(text)<=1000:
            raise ValueError('处理内容不能为空且不能超过 1,000 字符。请说明做了什么或处理依据。')
        if len(actor)>100:
            raise ValueError('处理人显示名不能超过 100 字符，请缩短名称。')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM exceptions WHERE exception_id=?',(exception_id,)).fetchone()
            if not row:
                raise ValueError('该异常不存在，请刷新页面重新选择。')
            if row['revision']!=expected_revision:
                raise StateConflict('该异常已有新的处理记录。请刷新页面，核对最新状态后再提交；本次内容尚未保存。')
            if to_status not in TRANSITIONS[row['processing_status']]:
                raise ValueError('不允许这次状态变更。已解决或已忽略的异常需先改为处理中并说明重新跟进原因。')
            timestamp=now()
            insert(db,'action_logs',dict(exception_id=exception_id,action_text=text,from_status=row['processing_status'],
                   to_status=to_status,actor_display_name=actor,acted_at=timestamp))
            db.execute('UPDATE exceptions SET processing_status=?,last_action_at=?,revision=revision+1 WHERE exception_id=?',
                       (to_status,timestamp,exception_id))
