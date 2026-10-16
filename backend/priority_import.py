"""Read only the requested XML sheets/columns; no Excel styles or formula execution."""
from __future__ import annotations

import io
import posixpath
import re
import zipfile
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

from fastapi import HTTPException
from opencc import OpenCC

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
CC = OpenCC('t2s')
FIELDS = ['sourceSequence', 'customerName', 'brokerAccountStatus', 'depositAmount',
          'onSiteOpener', 'insuranceBroker', 'zhongyangWitness', 'customerType',
          'agreementSigned', 'agreementAmountUsd', 'jiaoyangOwner', 'notes']
HEADERS = ['序号', '客户姓名', '是否开券商户', '入金金额', '骄阳现场开户人',
           '保险经纪人', '中阳见证人', '客户类型', '签署优先劣后协议',
           '优先劣后金额', '骄阳负责人', '备注说明']


def text(value):
    return str(value if value is not None else '').strip()


def normalized(value):
    return re.sub(r'\s+', '', CC.convert(text(value))).casefold()


def iso(value):
    parts = re.fullmatch(r'(20\d\d)[.\-/](\d{1,2})[.\-/](\d{1,2})', text(value))
    try:
        if not parts:
            raise ValueError()
        return date(*map(int, parts.groups())).isoformat()
    except ValueError:
        raise HTTPException(422, f'无效日期：{value}')


def number(value, label, *, nonnegative=False, integer=False):
    if value is None or text(value) == '':
        return None
    try:
        if isinstance(value, bool):
            raise InvalidOperation()
        result = Decimal(text(value).replace(',', ''))
        if not result.is_finite() or abs(result) > Decimal('1e18'):
            raise InvalidOperation()
        if (nonnegative and result < 0) or (integer and result != result.to_integral()):
            raise InvalidOperation()
        return format(result, 'f')
    except InvalidOperation:
        raise HTTPException(422, f'{label}的数值无法处理：{text(value)[:50]}；请修正该单元格后重传。')


def sum_values(values):
    return format(sum((Decimal(v) for v in values if v is not None), Decimal(0)), 'f')


def summary_number(value):
    """Read optional dashboard cells without making notes such as '/' numeric."""
    value = text(value).replace(',', '').replace('$', '')
    if not value or value in {'/', '-'}:
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return None
    return format(parsed, 'f') if parsed.is_finite() else None


def asset_summary(workbook, sheet):
    """Capture the optional F:I management summary beside the A:C roster."""
    cells = {}
    for index, values in workbook.rows(sheet, {'F', 'G', 'H', 'I'}, limit=120):
        label = text(values.get('F'))
        if label:
            cells.setdefault(normalized(label), []).append(dict(row=index, label=label, value=values.get('G'), share=values.get('H'), note=values.get('I')))

    def cell(label, first=0, last=120):
        return next((value for value in cells.get(normalized(label), []) if first <= value['row'] <= last), {})

    def item(label, count_label=None):
        row = cell(label)
        result = dict(label=label, count=summary_number(row.get('value')), share=summary_number(row.get('share')))
        if count_label:
            result['countLabel'] = count_label
        return result

    buckets = [item(label) for label in ('10万美金以上', '5-10万美金', '1-5万美金', '1500美金-1万美金', '1500美金以下', '无入金或已出金')]
    buckets = [row for row in buckets if row['count'] is not None]
    def pair(count_label, amount_label, first=0, last=120):
        count_row = cell(count_label, first, last)
        amount = summary_number(count_row.get('share'))
        if amount is None:
            amount = summary_number(count_row.get('note'))
        if amount is None:
            amount = summary_number(cell(amount_label, first, last).get('value'))
        return dict(count=summary_number(count_row.get('value')), amount=amount)

    def note_number(label, pattern, first=0, last=120):
        match = re.search(pattern, text(cell(label, first, last).get('note')))
        return summary_number(match.group(1)) if match else None

    return dict(
        assetBuckets=buckets,
        fundedAccounts=summary_number(cell('入金账户').get('value')),
        activationRate=summary_number(cell('入金激活率').get('value')),
        historicalPlacementUsd=summary_number(cell('历史已定增金额').get('value')),
        liveAccountAumUsd=summary_number(cell('实时账户总资产(USD)').get('value')),
        liveTotalAumUsd=summary_number(cell('实时总资产金额(USD)').get('value')),
        registration=dict(
            total=summary_number(cell('总注册开户数').get('value')),
            opened=summary_number(cell('开户成功数').get('value')),
            rate=summary_number(cell('开户成功率').get('value')),
        ),
        onsite=dict(
            signed=pair('签约总人数/金额', '签约总人数/金额', 27, 32),
            converted=pair('转化总人数/金额', '转化总人数/金额', 27, 32),
            dropped=pair('已放弃或转二级人数/金额', '已放弃或转二级人数/金额', 27, 32),
            pending=pair('待转化人数/金额', '待转化人数/金额', 27, 32),
        ),
        singleSign=dict(
            consulted=summary_number(cell('单签咨询人数', 33, 39).get('value')),
            converted=pair('转化总人数/金额', '转化总人数/金额', 33, 39),
            pending=dict(count=summary_number(cell('待转化人数', 33, 39).get('value')), amount=None),
        ),
        xmax=dict(
            holders=summary_number(cell('持仓人数', 40, 45).get('value')),
            holderShare=summary_number(cell('持仓人数占比', 40, 45).get('value')),
            quantity=summary_number(cell('持仓股数', 40, 45).get('value')),
            marketValue=summary_number(cell('持仓市值：$8.74/股', 40, 45).get('value')),
            blockHolders=note_number('持仓人数', r'大宗\s*(\d+)\s*人', 40, 45),
            blockQuantity=note_number('持仓股数', r'大宗[^\d]*(\d+)[^\d]*股', 40, 45),
            price='8.74',
        ),
        promisePlan=dict(
            signed=pair('意向签约人数', '意向签约人数', 46, 50),
            converted=pair('转化总人数/金额', '转化总人数/金额', 46, 50),
            pending=dict(count=summary_number(cell('待转化人数', 46, 50).get('value')), amount=summary_number(cell('待转化人数', 46, 50).get('share'))),
        ),
    )


def tw(value):
    result = text(value).upper()
    if not re.fullmatch(r'TW\d+', result):
        raise HTTPException(422, f'无法识别 TW 编号：{text(value)[:50]}')
    return result


class Workbook:
    def __init__(self, content):
        try:
            self.archive = zipfile.ZipFile(io.BytesIO(content))
            if len(self.archive.infolist()) > 3000 or sum(i.file_size for i in self.archive.infolist()) > 80_000_000:
                raise HTTPException(422, '工作簿展开后过大，请拆分文件。')
            rels = {r.get('Id'): r.get('Target') for r in ET.fromstring(self.archive.read('xl/_rels/workbook.xml.rels'))}
            root = ET.fromstring(self.archive.read('xl/workbook.xml'))
            self.sheets = []
            for s in root.findall('s:sheets/s:sheet', NS):
                target = rels.get(s.get(REL), '')
                path = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('xl', target))
                if path.startswith('xl/'):
                    self.sheets.append((s.get('name'), path))
            self.strings = []
            if 'xl/sharedStrings.xml' in self.archive.namelist():
                self.strings = [''.join(n.text or '' for n in s.iter('{'+NS['s']+'}t'))
                                for s in ET.fromstring(self.archive.read('xl/sharedStrings.xml'))]
        except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError) as exc:
            raise HTTPException(422, '文件不是可读取的 Excel 工作簿。') from exc

    def rows(self, sheet, columns=None, limit=None):
        root = ET.fromstring(self.archive.read(sheet[1]))
        output = []
        for r in root.findall('s:sheetData/s:row', NS):
            index = int(r.get('r'))
            if limit and index > limit:
                continue
            values = {}
            for c in r.findall('s:c', NS):
                col = re.sub(r'\d', '', c.get('r', ''))
                if columns is not None and col not in columns:
                    continue
                v = c.find('s:v', NS)
                v = v.text if v is not None else None
                kind = c.get('t')
                if kind == 's' and v is not None:
                    v = self.strings[int(v)]
                elif kind == 'inlineStr':
                    v = ''.join(n.text or '' for n in c.findall('s:is//s:t', NS))
                elif kind == 'b':
                    v = v == '1'
                if v is not None:
                    values[col] = v
            if values:
                output.append((index, values))
            if len(output) > 10000:
                raise HTTPException(422, '单个子表超过 10,000 行，请拆分。')
        return output


def parse(content, filename, as_of, batch_dates):
    workbook = Workbook(content)
    try:
        return _parse(workbook, filename, as_of, batch_dates)
    except (ET.ParseError, KeyError, IndexError, ValueError) as exc:
        raise HTTPException(422, f'{filename} 的表格结构无法识别，请检查原文件。') from exc
    finally:
        workbook.archive.close()


def _parse(workbook, filename, as_of, batch_dates):
    if not workbook.sheets:
        raise HTTPException(422, '没有找到工作表。')
    dated = []
    for sheet in workbook.sheets:
        if re.fullmatch(r'20\d\d[.\-/]\d{1,2}[.\-/]\d{1,2}', sheet[0]):
            dated.append((iso(sheet[0]), sheet))
    if dated:
        wanted = set(batch_dates)
        selected = [(d, s) for d, s in dated if d in wanted]
        if not selected:
            raise HTTPException(422, '找不到所选批次日期的子表；请在上传区填写本次活动日期。')
        datasets = []
        for batch_date, sheet in selected:
            raw = workbook.rows(sheet)
            header = next(((i, row) for i, row in raw[:20] if '客户姓名' in {normalized(v) for v in row.values()}
                           and '优先劣后金额' in {normalized(v) for v in row.values()}), None)
            if not header:
                raise HTTPException(422, f'{sheet[0]} 不是已识别的港安 ICC 格式。')
            # Recognize labels, so reordering business columns is safe.
            mapping = {}
            for field, label in zip(FIELDS, HEADERS):
                cols = [c for c, v in header[1].items() if normalized(v) == normalized(label)]
                if len(cols) != 1:
                    raise HTTPException(422, f'{sheet[0]} 缺少或重复表头：{label}')
                mapping[field] = cols[0]
            tw_cols = [c for c, v in header[1].items() if normalized(v) in {'tw', 'tw编号'}]
            if len(tw_cols) > 1:
                raise HTTPException(422, f'{sheet[0]} TW 编号表头重复。')
            records = []
            for index, values in raw:
                if index <= header[0]:
                    continue
                name = text(values.get(mapping['customerName']))
                if not name or name in {'合计', '总计'}:
                    continue
                r = {field: text(values.get(col)) for field, col in mapping.items()}
                code = text(values.get(tw_cols[0])) if tw_cols else ''
                r.update(sourceRow=index, sourceSheet=sheet[0], raw=values, twCode=tw(code) if code else '', batchDate=batch_date)
                r['agreementAmountUsd'] = number(r['agreementAmountUsd'], f'{sheet[0]}!J{index}', nonnegative=True)
                r['depositAmount'] = number(r['depositAmount'], f'{sheet[0]}!D{index}', nonnegative=True)
                r['recordKey'] = f'row:{index}'
                records.append(r)
            datasets.append(dict(kind='icc', date=batch_date, rows=records, sheet=sheet[0]))
        return datasets

    # Only the first sheet is authoritative for the client registry; only ABC for assets.
    sheet = workbook.sheets[0]
    raw = workbook.rows(sheet, {'A', 'B', 'C'})
    # Secondary-market exports vary by provider.  Keep the original NO./Cd
    # formats and also accept the common CODE / name export used by SXY.
    header = next(((i, r) for i, r in raw[:20] if normalized(r.get('A')) in {
        '客户姓名', '客户编码', 'no.', 'cd', 'code'
    }), None)
    if header is None:
        raise HTTPException(422, f'{filename} 不属于已配置的四种表格。')
    label = normalized(header[1].get('A'))
    kind = {'客户姓名': 'master', '客户编码': 'assets', 'no.': 'secondary', 'cd': 'secondary', 'code': 'secondary'}[label]
    if kind == 'master' and normalized(header[1].get('B')) != '是否完成开户':
        raise HTTPException(422, '客户名单表头发生变化，请核对券商开户状态列。')
    if kind == 'assets' and not normalized(header[1].get('C')).startswith('客户权益资产'):
        raise HTTPException(422, '客户资产列含义无法确认。')
    quantity_col, name_col = 'B', 'C'
    if kind == 'secondary':
        quantities = [c for c,v in header[1].items() if normalized(v) == 'qty']
        names = [c for c,v in header[1].items() if normalized(v) in {'client_acc_name', 'name'}]
        if len(quantities) != 1 or len(names) != 1 or 'A' in quantities + names:
            raise HTTPException(422, '二级持仓需包含唯一 qty 和 client_acc_name 列。')
        quantity_col, name_col = quantities[0], names[0]
    # A dated file must not silently be imported into a different week.
    match = re.search(r'(20\d\d)[.\-/]?(\d{2})[.\-/]?(\d{2})', filename)
    if match and date(*map(int, match.groups())).isoformat() != as_of:
        raise HTTPException(422, f'{filename} 的日期与统计日期 {as_of} 不一致。')
    records = []
    totals = defaultdict(list)
    holding_mode = 'secondary'
    groups = defaultdict(list)
    for index, values in raw:
        if index <= header[0]:
            continue
        marker = normalized(values.get('A'))
        if kind == 'secondary' and '大宗' in marker:
            holding_mode = 'block'
            if text(values.get(quantity_col)):
                totals[holding_mode].append(number(values[quantity_col], '大宗持仓合计', nonnegative=True, integer=True))
            continue
        if kind == 'secondary' and '二级买入' in marker:
            holding_mode = 'secondary'
            if text(values.get(quantity_col)):
                totals[holding_mode].append(number(values[quantity_col], '二级持仓合计', nonnegative=True, integer=True))
            continue
        if kind == 'secondary' and not text(values.get('A')) and not text(values.get(name_col)):
            if text(values.get(quantity_col)):
                totals[holding_mode].append(number(values[quantity_col], '持仓合计', nonnegative=True, integer=True))
            continue
        if not any(text(v) for v in values.values()):
            continue
        code = tw(values.get('C' if kind == 'master' else 'A'))
        r = dict(twCode=code, customerName=text(values.get('A' if kind == 'master' else 'B' if kind == 'assets' else name_col)),
                 recordKey=code, sourceRow=index, sourceSheet=sheet[0], raw=values)
        if kind == 'master':
            r['brokerAccountStatus'] = text(values.get('B'))
        else:
            field = 'assetUsd' if kind == 'assets' else 'quantity'
            r[field] = number(values.get('C' if kind == 'assets' else quantity_col), f'{sheet[0]} 第 {index} 行',
                              nonnegative=kind == 'secondary', integer=kind == 'secondary')
            if kind == 'secondary':
                r['holdingType'] = holding_mode
            if r[field] is None:
                raise HTTPException(422, f'{sheet[0]} 第 {index} 行缺少数值；空白不能作为零。')
        groups[code].append(r)
    for code, items in groups.items():
        if kind == 'master' and len(items) > 1:
            raise HTTPException(422, f'{sheet[0]} 中 {code} 重复，需核对后重传。')
        r = dict(items[0])
        if kind in {'assets', 'secondary'}:
            if len({normalized(x['customerName']) for x in items}) > 1:
                raise HTTPException(422, f'{code} 的账户姓名不一致，请核对。')
            field = 'assetUsd' if kind == 'assets' else 'quantity'
            r[field] = sum_values(x[field] for x in items)
            r['sourceRows'] = items
            if kind == 'secondary':
                r['secondaryQuantity'] = sum_values(x[field] for x in items if x.get('holdingType') == 'secondary')
                r['blockQuantity'] = sum_values(x[field] for x in items if x.get('holdingType') == 'block')
                types = sorted({x.get('holdingType') for x in items if x.get('holdingType')})
                r['holdingTypes'] = types
                r['holdingType'] = types[0] if len(types) == 1 else 'mixed'
        records.append(r)
    if kind == 'secondary':
        for mode, mode_totals in totals.items():
            detail_total = sum_values(r.get('blockQuantity' if mode == 'block' else 'secondaryQuantity', 0) for r in records)
            if mode_totals and any(Decimal(total) != Decimal(detail_total) for total in mode_totals):
                label = '大宗' if mode == 'block' else '二级'
                raise HTTPException(422, f'{label}持仓明细股数与表尾合计不一致，请核对来源文件。')
    if not records:
        raise HTTPException(422, f'{filename} 没有可导入记录。')
    return [dict(kind=kind, date=as_of, rows=records, sheet=sheet[0],
                 summary=asset_summary(workbook, sheet) if kind == 'assets' else {})]
