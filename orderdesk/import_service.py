import csv
import hashlib
import io
from dataclasses import dataclass

from .constants import ORDER_FIELDS, INVENTORY_FIELDS


class ImportProblem(ValueError):
    """可直接向用户展示的结构错误。"""


@dataclass
class SourceFile:
    source_type: str
    rows: list
    file_hash: str
    extra_columns: list
    ignored_blank_rows: int = 0


def parse_csv(content: bytes, source_type: str) -> SourceFile:
    title = '订单表' if source_type == 'ORDER' else '库存表'
    required = ORDER_FIELDS if source_type == 'ORDER' else INVENTORY_FIELDS
    if not content or len(content) > 10 * 1024 * 1024:
        raise ImportProblem(f'{title}为空或超过 10 MB。请上传非空、大小不超过 10 MB 的 CSV。')
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ImportProblem(f'{title}不是 UTF-8 编码。请在表格软件中另存为“CSV UTF-8”后重试。') from exc
    reader = csv.reader(io.StringIO(text, newline=''), strict=True)
    try:
        headers = next(reader, [])
        if not headers or len(headers) != len(set(headers)):
            raise ImportProblem(f'{title}没有表头或列名重复。请保留一行英文表头，删除重复列。')
        missing = [f for f in required if f not in headers]
        if missing:
            raise ImportProblem(f'{title}缺少必需列：{", ".join(missing)}。请使用页面提供的模拟 CSV 列名。')
        rows, blanks = [], 0
        for number, cells in enumerate(reader, 2):
            if not cells or not any(x.strip() for x in cells):
                blanks += 1
                continue
            if len(cells) != len(headers):
                raise ImportProblem(f'{title}第 {number} 条记录有 {len(cells)} 列，表头为 {len(headers)} 列。请补齐空单元格；含逗号的内容用双引号包裹。')
            if len(rows) >= 10000:
                raise ImportProblem(f'{title}超过 10,000 条非空记录。请拆分后重新导入。')
            raw = dict(zip(headers, cells))
            rows.append({'source_type': source_type, 'source_row_no': number,
                         'raw_values': raw, 'values': {k: v.strip() for k, v in raw.items()}, 'errors': []})
    except csv.Error as exc:
        raise ImportProblem(f'{title}在第 {reader.line_num} 行附近无法读取：{exc}。请检查引号和逗号，再另存为 CSV UTF-8。') from exc
    if not rows:
        raise ImportProblem(f'{title}没有非空数据记录。请在表头下添加数据。')
    known = set(required) | ({'actual_ship_at'} if source_type == 'ORDER' else set())
    return SourceFile(source_type, rows, hashlib.sha256(content).hexdigest(),
                      [x for x in headers if x not in known], blanks)
