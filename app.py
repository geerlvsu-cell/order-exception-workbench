"""启动：.venv/bin/python -m streamlit run app.py"""
import hashlib
import os
import sqlite3
from pathlib import Path

import streamlit as st

from orderdesk.constants import (DEMO_TIME, LABELS, TYPE_LABELS, STATUS_LABELS,
                                 SUPPLY_LABELS, TRANSITIONS, RULE_VERSION)
from orderdesk.demo import build_demo
from orderdesk.export_service import export_csv, export_name
from orderdesk.import_service import ImportProblem
from orderdesk.repository import Repository, StateConflict
from orderdesk.rules import analyze, filter_exceptions

ROOT = Path(__file__).resolve().parent
PAGES = ['导入与校验', '异常工作台', '库存供需汇总']
PAGE_CODES = dict(zip(PAGES, ['import', 'exceptions', 'inventory']))


def readable_rows(rows):
    return [{LABELS.get(k, '案例说明' if k == 'case_label' else k): v for k, v in row.items()} for row in rows]


def object_name(e):
    if e['target_kind'] == 'STOCK_GROUP':
        return f"{e['warehouse_id']} / {e['sku_id']}"
    if e['target_kind'] == 'INVENTORY_ROW':
        return f"库存表第 {e.get('source_row_no') or e['evidence'].get('source_row_no')} 条记录"
    name=f"{e['order_id']} · 行 {e['line_id'] or '缺失'}" if e['order_id'] else '订单号缺失或不合法'
    if e['target_kind']=='ORDER_ROW':
        name+=f" · 来源第 {e.get('source_row_no') or e['evidence'].get('source_row_no')} 条"
    return name


def coverage(metrics):
    if metrics['invalid_order_row_count']:
        st.warning(f"有 {metrics['invalid_order_row_count']} 条订单记录未参与超时与需求计算，已知需求可能偏小。请先核对数据质量问题。")
    if not metrics['valid_order_line_count']:
        st.info('无可用于业务判断的订单行。请补齐或修正源表后，作为新批次导入。')


def import_page(repo):
    st.subheader('导入与校验')
    st.write('准备同一时点的订单明细与库存表，先检查数据，再保存识别结果。')
    mode = st.radio('数据来源', ['内置模拟数据（300行）', '上传模拟 CSV'], horizontal=True, key='import_mode')
    sample_orders, sample_stock = build_demo()
    with st.expander('下载模拟数据与查看字段说明'):
        left, right = st.columns(2)
        left.download_button('下载模拟订单明细', sample_orders, '模拟数据_订单明细.csv', 'text/csv', key='sample_orders')
        right.download_button('下载模拟库存', sample_stock, '模拟数据_库存.csv', 'text/csv', key='sample_stock')
        st.caption('固定种子 20261121；共 300 条订单记录、18 条库存记录。含正常、超时、取消、全部发货、字段缺失、错误日期、重复和库存不足等案例。案例说明列不参与判断。')
        st.write('分析时点：北京时间。数量单位：件。可用库存尚未扣减本批待发需求。')
        st.write('订单状态：ACTIVE 表示履约中，CANCELLED 表示取消剩余履约，SHIPPED 表示全部发完。ACTIVE 行若已发量等于订购量，同样视为发完。')
        st.write('最近实际发货时间表示最近一次发货，不代表已经全部发完。旧版九列订单模板仍兼容；提供此列时会检查它与已发数量的一致性。')
        st.write('CSV 需使用英文列名和 UTF-8 编码；日期格式为 YYYY-MM-DD HH:MM:SS。两份文件均为模拟数据。')
    order_name, stock_name = '模拟数据_订单明细.csv', '模拟数据_库存.csv'
    orders, stock = sample_orders, sample_stock
    if mode == '上传模拟 CSV':
        a, b = st.columns(2)
        uploaded_orders = a.file_uploader('订单明细 CSV（模拟数据）', type=['csv'], key='order_upload')
        uploaded_stock = b.file_uploader('库存 CSV（模拟数据）', type=['csv'], key='stock_upload')
        orders = uploaded_orders.getvalue() if uploaded_orders else None
        stock = uploaded_stock.getvalue() if uploaded_stock else None
        order_name = uploaded_orders.name if uploaded_orders else order_name
        stock_name = uploaded_stock.name if uploaded_stock else stock_name
    at = st.text_input('分析时点（北京时间）', value=DEMO_TIME, help='固定时点保证结果可复核。库存表各行 snapshot_at 必须等于此时点；订单表也应反映这个时点的快照。', key='analysis_at_input')
    st.caption('两份源表必须反映同一时点。仅修改分析时点，不会让旧库存自动成为新时点的库存。')
    signature = (hashlib.sha256(orders or b'').hexdigest(),hashlib.sha256(stock or b'').hexdigest(),at,order_name,stock_name)
    if st.session_state.get('input_signature') != signature:
        st.session_state.pop('import_error', None)
        st.session_state['input_signature'] = signature
    if st.button('检查数据', type='primary', disabled=not orders or not stock):
        st.session_state.pop('preview', None)
        try:
            with st.spinner('正在校验字段并识别异常…'):
                st.session_state['preview'] = analyze(orders, stock, at, order_name, stock_name)
                st.session_state['preview_signature'] = signature
                st.session_state.pop('import_error', None)
        except ImportProblem as exc:
            st.session_state['import_error'] = str(exc)
    if st.session_state.get('import_error'):
        st.error(st.session_state['import_error'])
    preview = st.session_state.get('preview')
    if not preview or st.session_state.get('preview_signature') != signature:
        if preview:
            st.info('文件或分析时点已变更，请重新点击“检查数据”，确认结果后保存。')
        elif orders and stock:
            st.info('点击“检查数据”查看预览。记录级数据问题会保留供跟进，不会被静默删除。')
        return
    m = preview.metrics
    a, b, c = st.columns(3)
    a.metric('订单记录', f"{m['input_order_row_count']} 条")
    b.metric('无效订单记录', f"{m['invalid_order_row_count']} 条")
    c.metric('数据质量问题', f"{m['data_quality_issue_count']} 项")
    coverage(m)
    st.caption(f"已识别超时 {m['overdue_line_count']} 行、库存缺口 {m['stock_gap_group_count']} 组、库存无法判断 {m['unknown_stock_group_count']} 组。")
    for source, extra in preview.extra_columns.items():
        if extra:
            st.caption(f"{'订单' if source == 'ORDER' else '库存'}表额外列不参与计算：{', '.join(extra)}。")
    ignored = preview.batch['ignored_blank_order_rows'] + preview.batch['ignored_blank_inventory_rows']
    if ignored:
        st.caption(f'忽略 {ignored} 条全空记录；其他不完整记录均保留。来源序号按 CSV 记录编号，表头为 1。')
    tabs = st.tabs(['订单预览', '库存预览', '校验问题'])
    for tab, source in zip(tabs[:2], ['ORDER', 'INVENTORY']):
        with tab:
            st.dataframe(readable_rows([r['raw_values'] for r in preview.raw_rows if r['source_type'] == source]), hide_index=True, width='stretch', height=300)
    with tabs[2]:
        problems = [{'来源': '订单表' if r['source_type']=='ORDER' else '库存表', '记录序号': r['source_row_no'],
                     '字段': LABELS.get(e['field'], e['field']), '问题与修改方法': e['message']}
                    for r in preview.raw_rows for e in r['errors']]
        if problems:
            st.dataframe(problems[:200], hide_index=True, width='stretch')
            if len(problems)>200:
                st.caption(f'预览前 200 个原因，共 {len(problems)} 个。保存后可按异常对象查看全部依据。')
        else:
            st.success('输入记录的字段与关联校验通过。库存匹配缺失等组合问题可在工作台查看。')
    st.caption('保存后快照不可直接改写。相同文件、时点与规则再次导入会打开原批次并保留处理进展；新快照不继承旧处理状态。')
    if st.button('保存批次并进入工作台', type='primary'):
        try:
            batch_id, created = repo.save_analysis(preview)
            st.session_state['next_batch'] = batch_id
            st.session_state['next_page'] = '异常工作台'
            st.session_state['flash'] = '批次已保存，识别结果已就绪。' if created else '检测到重复导入，已打开原批次；原有状态和处理记录均保留。'
            st.rerun()
        except sqlite3.Error:
            st.error('本地批次保存失败，本次没有保存部分结果。请确认项目 data 文件夹可写并稍后重试。')


def overview(metrics):
    st.subheader('本批次总览')
    cards=[('输入订单数','input_order_count','张'),('输入订单行记录数','input_order_row_count','条'),
           ('超时订单数','overdue_order_count','张'),('超时订单行数','overdue_line_count','条'),
           ('库存缺口组合数','stock_gap_group_count','组'),('数据质量问题数','data_quality_issue_count','项')]
    for start in (0,3):
        for column,(label,key,unit) in zip(st.columns(3),cards[start:start+3]):
            with column.container(border=True):
                st.metric(label,f'{metrics[key]} {unit}')
    st.caption(f"可参与判断 {metrics['valid_order_line_count']} 行 · 待跟进 {metrics['open_exception_count']} 项 · 库存无法判断 {metrics['unknown_stock_group_count']} 组 · 无效库存 {metrics['invalid_inventory_row_count']} 条 · 订单号无法归属 {metrics['unidentifiable_order_row_count']} 条")
    st.caption('总览不随列表筛选变化。输入行数包含重复与无效记录；库存风险按仓库＋商品计数，不是缺货订单数。')
    coverage(metrics)


def evidence_view(item,batch):
    st.subheader(f"{TYPE_LABELS[item['exception_type']]} · {object_name(item)}")
    st.caption(f"异常编号 {item['exception_id']} · 当前状态：{STATUS_LABELS[item['processing_status']]}")
    st.write(item['trigger_summary'])
    ev=item['evidence']
    if item['exception_type']=='DATA_QUALITY' and 'errors' in ev:
        name=batch['order_file_name'] if ev['source_type']=='ORDER' else batch['inventory_file_name']
        st.caption(f"来源：{name}，第 {ev['source_row_no']} 条记录（表头为 1）")
        st.dataframe([{'字段':LABELS.get(x['field'],x['field']),'原始值':str(x['raw_value']) if x['raw_value'] is not None else '空白','问题与修改方法':x['message']} for x in ev['errors']],hide_index=True,width='stretch')
        with st.expander('查看原始记录'):
            st.dataframe([{'字段':LABELS.get(k,k),'原始内容':str(v)} for k,v in ev['raw_values'].items()],hide_index=True,width='stretch')
    elif item['exception_type']=='OVERDUE':
        a,b,c=st.columns(3)
        a.metric('订购',f"{ev['ordered_qty']} 件")
        b.metric('已发',f"{ev['shipped_qty']} 件")
        c.metric('未发',f"{ev['remaining_qty']} 件")
        st.write(f"承诺发货：{ev['promised_ship_at']}；分析时点：{ev['analysis_at']}；超时 {ev['overdue_seconds']/3600:g} 小时。")
        st.caption(f"来源：{batch['order_file_name']}，第 {ev['source_row_no']} 条记录。规则：未取消、仍有未发数量，并且分析时点严格晚于承诺时间。")
        if ev.get('actual_ship_at'):
            st.caption(f"最近实际发货时间：{ev['actual_ship_at']}。本条仍有剩余数量待发。")
    else:
        a,b,c=st.columns(3)
        a.metric('有效未发需求',f"{ev['demand_qty']} 件")
        b.metric('对本批可用库存','无法判断' if ev['available_qty'] is None else f"{ev['available_qty']} 件")
        c.metric('已知缺口','无法判断' if ev['gap_qty'] is None else f"{ev['gap_qty']} 件")
        st.info('这是仓库与商品层面的供需比较，尚未把库存分配到具体订单，不能据此认定某笔订单必然缺货。')
        st.dataframe([{'订单号':x['order_id'],'行号':x['line_id'],'未发数量':x['remaining_qty'],'订单来源记录':x['source_row_no']} for x in ev['demand_lines']],hide_index=True,width='stretch')
        if ev['inventory_sources']:
            st.caption(f"库存来源：{batch['inventory_file_name']}，记录序号：{', '.join(str(x['source_row_no']) for x in ev['inventory_sources'])}。")
        else:
            st.caption(f"库存来源：{batch['inventory_file_name']} 无此仓库商品组合的匹配记录；请补齐同一时点的数据。")
    if item['processing_status'] in ('RESOLVED','IGNORED'):
        st.caption('人工处理状态已更新。该批次的原始触发依据仍保留，后续数据是否消除问题需通过新快照核对。')
    with st.expander('完整触发依据'):
        st.json(ev,expanded=False)


def action_form(repo,item,batch,public_mode=False):
    st.markdown('#### 记录处理进展')
    with st.form(f"action_{item['exception_id']}_{item['revision']}"):
        a,b=st.columns(2)
        target=a.selectbox('处理状态',TRANSITIONS[item['processing_status']],format_func=STATUS_LABELS.get)
        actor=b.text_input('处理人显示名',value='演示访客' if public_mode else '本地演示用户',max_chars=100)
        note=st.text_area('处理动作或依据',placeholder='例如：已联系仓库核对库存，约定下午回复。已解决或忽略时，请写明依据；重新跟进时请说明原因。',max_chars=1000)
        submitted=st.form_submit_button('保存记录与状态',type='primary')
    if submitted:
        try:
            repo.add_action(item['exception_id'],target,note,actor,item['revision'])
            st.session_state['flash']='处理记录与状态已保存在本次体验中。' if public_mode else '处理记录与状态已保存到本地。'
            st.rerun()
        except (ValueError,StateConflict) as exc:
            st.error(str(exc))
        except sqlite3.Error:
            st.error('处理记录保存失败，状态未变更。请确认本地数据文件可写，稍后重试。')
    history=repo.actions(batch['batch_id'],[item['exception_id']])
    st.markdown('#### 已保存的处理记录')
    if not history:
        st.caption('尚无处理记录。')
    else:
        for record in history:
            with st.container(border=True):
                st.caption(f"{record['acted_at']} · {record['actor_display_name']} · {STATUS_LABELS[record['from_status']]} → {STATUS_LABELS[record['to_status']]}")
                st.write(record['action_text'])


def exception_exports(repo,batch,all_items,filtered,filters):
    with st.expander('导出异常与处理记录'):
        scope=st.radio('异常导出范围',['当前筛选','本批次全部'],horizontal=True,key=f"export_scope_{batch['batch_id']}")
        chosen=filtered if scope=='当前筛选' else all_items
        scope_code='FILTERED' if scope=='当前筛选' else 'FULL_BATCH'
        applied=filters if scope_code=='FILTERED' else {}
        logs=repo.actions(batch['batch_id'],[x['exception_id'] for x in chosen])
        a,b=st.columns(2)
        a.download_button(f'下载异常清单（{len(chosen)}项）',export_csv('exceptions',chosen,batch,scope_code,applied),export_name('exceptions',batch),'text/csv',key='exceptions_download',on_click='ignore')
        b.download_button(f'下载处理记录（{len(logs)}条）',export_csv('actions',logs,batch,scope_code,applied),export_name('actions',batch),'text/csv',key='actions_download',on_click='ignore')
        st.caption('处理记录导出上述异常的全部历史动作。CSV 保留英文列名、规则依据和模拟标识；空结果导出表头。')


def workbench(repo,batch,public_mode=False):
    overview(repo.metrics(batch['batch_id']))
    st.divider()
    st.subheader('异常列表')
    all_items=repo.exceptions(batch['batch_id'])
    prefix=f"filter_{batch['batch_id']}_"
    a,b,c,d=st.columns(4)
    types=a.multiselect('异常类型',list(TYPE_LABELS),format_func=TYPE_LABELS.get,key=prefix+'types',placeholder='不限')
    statuses=b.multiselect('处理进展',list(STATUS_LABELS),format_func=STATUS_LABELS.get,key=prefix+'statuses',placeholder='不限')
    warehouses=c.multiselect('仓库',sorted({x['warehouse_id'] for x in all_items if x['warehouse_id']})+([None] if any(x['warehouse_id'] is None for x in all_items) else []),format_func=lambda x:x or '缺失或不合法',key=prefix+'warehouses',placeholder='不限')
    skus=d.multiselect('商品编码',sorted({x['sku_id'] for x in all_items if x['sku_id']})+([None] if any(x['sku_id'] is None for x in all_items) else []),format_func=lambda x:x or '缺失或不合法',key=prefix+'skus',placeholder='不限')
    a,b=st.columns([4,1])
    order=a.text_input('订单号精确查询',placeholder='例如 SIM-1001；不查询组合级风险',key=prefix+'order')
    def clear_filters():
        for key in ['types','statuses','warehouses','skus']:
            st.session_state[prefix+key]=[]
        st.session_state[prefix+'order']=''
    b.write('')
    b.button('清空筛选',on_click=clear_filters,width='stretch')
    filters=dict(types=types,statuses=statuses,warehouses=warehouses,skus=skus,order_id=order.strip())
    filtered=filter_exceptions(all_items,**filters)
    st.caption(f'当前筛选 {len(filtered)} 项 / 本批次 {len(all_items)} 项。不同筛选条件取交集；同一条件多选取并集。')
    if filtered:
        st.dataframe([{'类型':TYPE_LABELS[x['exception_type']],'对象':object_name(x),'仓库':x['warehouse_id'] or '缺失或不合法',
                       '商品编码':x['sku_id'] or '缺失或不合法','触发摘要':x['trigger_summary'],
                       '处理状态':STATUS_LABELS[x['processing_status']],'最近处理':x['last_action_at'] or '尚未处理'} for x in filtered],
                     hide_index=True,width='stretch',height=min(400,75+35*len(filtered)))
    else:
        st.info('此范围没有异常项。可以清空筛选或切换批次；未触发异常不代表被排除的数据正常。')
    exception_exports(repo,batch,all_items,filtered,filters)
    if not filtered:
        return
    by_id={x['exception_id']:x for x in filtered}
    selected=st.selectbox('选择一项查看依据和处理记录',list(by_id),format_func=lambda k:f"{TYPE_LABELS[by_id[k]['exception_type']]} | {object_name(by_id[k])}",key=f"selected_exception_{batch['batch_id']}")
    item=by_id[selected]
    st.divider()
    evidence_view(item,batch)
    action_form(repo,item,batch,public_mode)


def inventory_page(repo,batch):
    st.subheader('库存供需汇总')
    st.write('先把同仓同商品的有效未发需求相加，再与本批可用库存比较。')
    metrics=repo.metrics(batch['batch_id'])
    coverage(metrics)
    all_rows=repo.inventory_checks(batch['batch_id'])
    a,b,c=st.columns(3)
    wh=a.multiselect('仓库',sorted({x['warehouse_id'] for x in all_rows}),key=f"stock_wh_{batch['batch_id']}",placeholder='不限')
    sku=b.multiselect('商品编码',sorted({x['sku_id'] for x in all_rows}),key=f"stock_sku_{batch['batch_id']}",placeholder='不限')
    states=c.multiselect('判断结果',list(SUPPLY_LABELS),format_func=SUPPLY_LABELS.get,key=f"stock_state_{batch['batch_id']}",placeholder='不限')
    filtered=[x for x in all_rows if (not wh or x['warehouse_id'] in wh) and (not sku or x['sku_id'] in sku) and (not states or x['supply_status'] in states)]
    st.caption(f'当前显示 {len(filtered)} 组。只包含有正数有效未发需求的组合；未知库存不计为零库存。')
    if filtered:
        st.dataframe([{'仓库':x['warehouse_id'],'商品编码':x['sku_id'],'有效未发需求（件）':str(x['demand_qty']),
                       '可用库存（件）':'无法判断' if x['available_qty'] is None else str(x['available_qty']),
                       '已知缺口（件）':'无法判断' if x['gap_qty'] is None else str(x['gap_qty']),
                       '判断结果':SUPPLY_LABELS[x['supply_status']],'关联需求行数':x['demand_line_count'],
                       '关联需求订单数':x['demand_order_count']} for x in filtered],hide_index=True,width='stretch')
    else:
        st.info('当前没有符合条件的有效需求组合。请调整筛选或先核对订单数据质量。')
    filters={k:v for k,v in dict(warehouses=wh,skus=sku,supply_statuses=states).items() if v}
    st.download_button(f'下载库存供需汇总（{len(filtered)}组）',export_csv('inventory',filtered,batch,'FILTERED' if filters else 'FULL_BATCH',filters,metrics['invalid_order_row_count']),export_name('inventory',batch),'text/csv',key='inventory_download',on_click='ignore')
    st.caption('此导出只使用本页筛选，与异常页的处理状态无关。库存无法判断时，数值列为空并保留判断结果。')
    if filtered:
        pairs={f"{x['warehouse_id']} / {x['sku_id']}":x for x in filtered}
        choice=st.selectbox('查看需求组成',list(pairs),key=f"stock_detail_{batch['batch_id']}")
        x=pairs[choice]
        with st.expander('展开需求明细与库存来源',expanded=True):
            st.write(SUPPLY_LABELS[x['supply_status']])
            st.caption('关联订单仅表示参与需求汇总，不代表这些订单都已确定缺货。')
            st.dataframe([{'订单号':r['order_id'],'行号':r['line_id'],'未发数量':r['remaining_qty'],'订单来源记录':r['source_row_no']} for r in x['evidence']['demand_lines']],hide_index=True,width='stretch')
            for source in x['evidence']['inventory_sources']:
                st.caption(f"库存来源：{batch['inventory_file_name']} 第 {source['source_row_no']} 条记录。")
                for error in source['errors']:
                    st.write(error['message'])


def main(public_mode=False):
    st.set_page_config(page_title='订单异常处理工作台 · 模拟数据',page_icon='📦',layout='wide')
    st.markdown('<style>.block-container{padding-top:2rem;padding-bottom:3rem} [data-testid="stMetricValue"]{font-size:2rem} [data-testid="stSidebar"]{border-right:1px solid #e2e8ed}</style>',unsafe_allow_html=True)
    try:
        if public_mode:
            from orderdesk.session_repository import SessionRepository
            if 'public_repository' not in st.session_state:
                st.session_state['public_repository']=SessionRepository()
                demo=analyze(*build_demo(),DEMO_TIME)
                st.session_state['public_repository'].save_analysis(demo)
            repo=st.session_state['public_repository']
        else:
            repo=Repository(os.environ.get('ORDER_DESK_DB',str(ROOT/'data'/'workbench.sqlite3')))
        batches=repo.list_batches()
    except sqlite3.Error:
        st.error('本地数据文件无法打开。请确认项目 data 文件夹可读写，再重新启动。')
        st.stop()
    if 'next_page' in st.session_state:
        st.session_state['page_nav']=st.session_state.pop('next_page')
    if 'next_batch' in st.session_state:
        st.session_state['batch_selector']=st.session_state.pop('next_batch')
    requested=st.query_params.get('page')
    default_page=next((p for p,c in PAGE_CODES.items() if c==requested), '异常工作台' if batches else '导入与校验')
    with st.sidebar:
        st.markdown('### 订单异常工作台')
        st.caption('模拟数据 · 访客独立体验' if public_mode else '模拟数据 · 仅在本机运行')
        page=st.radio('工作区',PAGES,index=PAGES.index(default_page),key='page_nav')
        batch=None
        if batches:
            by_id={b['batch_id']:b for b in batches}
            wanted=st.query_params.get('batch')
            default_id=wanted if wanted in by_id else batches[0]['batch_id']
            selected=st.selectbox('已保存批次',list(by_id),index=list(by_id).index(default_id),format_func=lambda k:f"{by_id[k]['analysis_at']} · {k[:8]}",key='batch_selector')
            batch=by_id[selected]
            st.query_params['batch']=selected
            st.caption(f"保存于 {batch['created_at']}。相同输入会返回原批次；新快照不会继承旧状态。")
        st.divider()
        st.caption('先检查数据，再判断业务异常。所有时间按北京时间解释。')
    st.query_params['page']=PAGE_CODES[page]
    st.title('订单异常处理工作台')
    st.caption('个人练习项目 · 模拟数据演示 · 检查订单、追溯依据、记录处理进展')
    if public_mode:
        st.info('公开体验版：已为你加载300条模拟订单。可筛选、查看依据、记录处理和导出。你的练习数据与其他访客隔离；刷新或关闭页面将重新开始，需要保留时请先导出。仅上传虚构模拟数据。')
    if batch and page!='导入与校验':
        st.info(f"模拟数据 ｜ 分析时点 {batch['analysis_at']}（北京时间） ｜ 批次 {batch['batch_id'][:8]} ｜ 规则 {batch['rule_version']}")
    else:
        st.caption(f'当前规则 {RULE_VERSION} · 所有上传内容仅用于本地模拟演示')
    if st.session_state.get('flash'):
        st.success(st.session_state.pop('flash'))
    if page=='导入与校验':
        import_page(repo)
    elif not batch:
        st.info('尚无已保存批次。请在“导入与校验”中检查并保存模拟数据。')
    elif page=='异常工作台':
        workbench(repo,batch,public_mode)
    else:
        inventory_page(repo,batch)


if __name__=='__main__':
    main()
