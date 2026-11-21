import csv
import io
from .repository import now
from .rules import json_text

META=['batch_id','analysis_at','timezone','rule_version','data_mode','export_scope','filters_json','exported_at']
EXCEPTION_FIELDS=['exception_id','exception_type','target_kind','order_id','line_id','warehouse_id','sku_id',
                  'source_type','source_row_no','reason_codes_json','trigger_summary','evidence_json',
                  'processing_status','detected_at','last_action_at']
ACTION_FIELDS=['action_id','exception_id','exception_type','target_kind','order_id','line_id','warehouse_id','sku_id',
               'action_text','from_status','to_status','actor_display_name','acted_at']
STOCK_FIELDS=['warehouse_id','sku_id','demand_qty','available_qty','gap_qty','supply_status','demand_line_count',
              'demand_order_count','batch_invalid_order_row_count','evidence_json']


def export_csv(kind,rows,batch,scope='FILTERED',filters=None,invalid_order_rows=0):
    fields={'exceptions':EXCEPTION_FIELDS,'actions':ACTION_FIELDS,'inventory':STOCK_FIELDS}[kind]
    metadata={k:batch[k] for k in META[:5]}
    metadata.update(export_scope=scope,filters_json=json_text(filters or {}),exported_at=now())
    output=io.StringIO(newline='')
    writer=csv.DictWriter(output,fieldnames=META+fields,lineterminator='\n')
    writer.writeheader()
    for row in rows:
        item={k:row.get(k) for k in fields}
        if 'evidence_json' in fields:
            item['evidence_json']=json_text(row['evidence'])
        if 'reason_codes_json' in fields:
            item['reason_codes_json']=json_text(row['reason_codes'])
        if kind=='inventory':
            item['batch_invalid_order_row_count']=invalid_order_rows
        item.update(metadata)
        writer.writerow(item)
    return output.getvalue().encode('utf-8-sig')


def export_name(kind,batch):
    label={'exceptions':'异常清单','actions':'处理记录','inventory':'库存供需汇总'}[kind]
    return f"模拟数据_{label}_{batch['batch_id'][:8]}_{now().replace('-','').replace(':','').replace(' ','_')}.csv"
