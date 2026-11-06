"""Portable vector PDF export, generated from the same authorized report payload."""
from io import BytesIO
from datetime import date
from xml.sax.saxutils import escape
import os


class ReportFontUnavailable(RuntimeError):
    """Export requires a Chinese font that can actually be embedded."""


def register_report_font():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    # Noto CJK's usual Linux OTF/TTC packages contain CFF outlines, which
    # ReportLab's TTFont cannot embed. WenQuanYi supplies TrueType outlines.
    configured = os.environ.get('JY_REPORT_FONT', '')
    candidates = [configured] if configured else [
        '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
        '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
        '/System/Library/Fonts/STHeiti Light.ttc',
        '/System/Library/Fonts/Supplemental/Songti.ttc',
    ]
    for path in candidates:
        if not path or not os.path.isfile(path):
            continue
        try:
            pdfmetrics.registerFont(TTFont('JYReportCJK', path))
            return 'JYReportCJK'
        except Exception as exc:
            # Try the next installed font, but never emit a PDF whose Chinese
            # text relies on a font pack on the recipient's computer.
            if configured:
                raise ReportFontUnavailable('配置的报告中文字体无法嵌入，请配置 TrueType 中文字体。') from exc
    raise ReportFontUnavailable('PDF 中文字体未安装；Ubuntu 请安装 fonts-wqy-zenhei 后重试。')


def render_report(data):
    # Lazy imports keep the rest of the CRM available during dependency upgrades.
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, PageBreak, Flowable, KeepTogether

    cjk_font = register_report_font()
    ink, blue, gold, muted, rule = [colors.HexColor(c) for c in ('#13283f','#315f8c','#997638','#7c8795','#e5dfd5')]
    palette = [colors.HexColor(c) for c in ('#1c3c60','#315f8c','#4d7fab','#8faec9','#cbdbe7')]
    body = ParagraphStyle('body', fontName=cjk_font, fontSize=8.3, leading=13, textColor=ink, wordWrap='CJK')
    tiny = ParagraphStyle('tiny', parent=body, fontSize=7, leading=10, textColor=muted)
    right = ParagraphStyle('right', parent=body, alignment=TA_RIGHT)
    title = ParagraphStyle('title', parent=body, fontSize=24, leading=33, spaceAfter=7)
    heading = ParagraphStyle('heading', parent=body, fontSize=17, leading=23, spaceAfter=4, keepWithNext=True)
    eyebrow = ParagraphStyle('eyebrow', parent=body, fontName='Helvetica', fontSize=7, leading=11, textColor=gold, spaceBefore=15, keepWithNext=True)
    def fmt(value, digits=0):
        return '—' if value is None else f'{float(value):,.{digits}f}'
    def money(value, digits=0):
        return '—' if value is None else '$'+fmt(value,digits)
    def percent(value):
        return '—' if value is None else f'{float(value):.2f}%'
    def ratio(a,b):
        return percent(a/b*100) if a is not None and b else '—'
    def p(value, style=body):
        return Paragraph(escape(str(value if value is not None else '—')),style)
    def rich(value, style=body):
        return Paragraph(value,style)
    def section(number, english, name, note):
        return [p(f'{number} / {english}',eyebrow),p(name,heading),p(note,tiny),Spacer(1,10)]
    def table(rows, widths, header=True):
        result=Table(rows,colWidths=widths,repeatRows=1 if header else 0,hAlign='LEFT')
        commands=[('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),6),
                  ('RIGHTPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),7),
                  ('BOTTOMPADDING',(0,0),(-1,-1),7),('LINEBELOW',(0,0),(-1,-1),.35,rule)]
        if header: commands += [('LINEBELOW',(0,0),(-1,0),.7,gold)]
        result.setStyle(TableStyle(commands))
        return result

    class Donut(Flowable):
        def __init__(self):
            Flowable.__init__(self)
            self.width, self.height = 150, 176
        def draw(self):
            c=self.canv
            values=[max(0,float(b['amount'])) for b in data['assets']['buckets']]
            total=sum(values)
            angle=90
            for i,value in enumerate(values):
                if value and total:
                    extent=value/total*360
                    c.setFillColor(palette[i%len(palette)])
                    c.wedge(3,25,147,169,startAng=angle,extent=extent,fill=1,stroke=0)
                    angle+=extent
            c.setFillColor(colors.white);c.circle(75,97,49,fill=1,stroke=0)
            c.setFillColor(ink);c.setFont('Times-Roman',26);c.drawCentredString(75,98,fmt(data['assets']['accounts']))
            c.setFont(cjk_font,8);c.setFillColor(muted);c.drawCentredString(75,81,'账户数')
            c.drawCentredString(75,9,'圆环按正资产金额分布')

    class Progress(Flowable):
        def __init__(self,value,total,color=blue):
            Flowable.__init__(self);self.value=value;self.total=total;self.color=color
            self.width, self.height = 252, 21
        def draw(self):
            c=self.canv;c.setFillColor(colors.HexColor('#eeeae3'));c.rect(0,4,self.width,13,fill=1,stroke=0)
            if self.total and self.value is not None:
                c.setFillColor(self.color);c.rect(0,4,self.width*min(1,max(0,self.value/self.total)),13,fill=1,stroke=0)

    class Trend(Flowable):
        def __init__(self,key,label,color):
            Flowable.__init__(self);self.key=key;self.label=label;self.color=color
            self.width, self.height = 247, 152
        def draw(self):
            c=self.canv;c.setFillColor(ink);c.setFont(cjk_font,9);c.drawString(0,141,self.label)
            series=data.get('trend',[])
            available=[(i,float(r[self.key])) for i,r in enumerate(series) if r.get(self.key) is not None]
            if not available:
                c.setFont(cjk_font,8);c.setFillColor(muted);c.drawString(0,110,'暂无可绘制的历史来源');return
            latest=available[-1]
            c.setFont('Times-Roman',15);c.drawString(0,122,fmt(latest[1]))
            xs=[date.fromisoformat(r['date']).toordinal() for r in series]
            start=min(xs);span=max(1,max(xs)-start);lo=min(0,min(v for _,v in available));hi=max(1,max(v for _,v in available));scale=max(1,hi-lo)
            x=lambda i:39+197*(xs[i]-start)/span
            y=lambda v:22+82*(v-lo)/scale
            c.setLineWidth(.35)
            for fraction in (0,.5,1):
                value=lo+scale*fraction;c.setStrokeColor(rule);c.line(39,y(value),236,y(value))
                short=f'{value/1e6:.1f}m' if abs(value)>=1e6 else f'{value/1000:.1f}k' if abs(value)>=1e4 else fmt(value)
                c.setFillColor(muted);c.setFont('Helvetica',6);c.drawRightString(34,y(value)-2,short)
            c.setStrokeColor(self.color);c.setLineWidth(1.5);previous=None
            for i,r in enumerate(series):
                if r.get(self.key) is None:previous=None;continue
                v=float(r[self.key]);pos=(x(i),y(v))
                if previous:c.line(*previous,*pos)
                c.setFillColor(self.color);c.circle(*pos,2,fill=1,stroke=0);previous=pos
            for i in sorted({available[0][0],available[len(available)//2][0],latest[0]}):
                c.setFillColor(muted);c.setFont('Helvetica',6);c.drawCentredString(x(i),9,series[i]['date'][5:])

    stream=BytesIO()
    doc=SimpleDocTemplate(stream,pagesize=(595.28,841.89),rightMargin=40,leftMargin=40,topMargin=33,bottomMargin=37,
                          title='港安客户资产与业务经营报告 '+data['asOf'],author='JY Customer Operations')
    width=515.28
    story=[]
    k,a,h=data['kpis'],data['assets'],data['holdings']
    historical,products=data['historical'],data['products']
    story += [p('JY INVESTOR BRIEFING / '+data['asOf'][:4],eyebrow),p('港安客户资产与业务经营报告',title),
              p('资产结构、客户转化与在售产品的经营全景',tiny),Spacer(1,12)]
    source_names=dict(master='客户名单',assets='资产明细',secondary='分类持仓',icc='ICC 活动')
    source_text=' · '.join(f'{source_names[key]} {value}' for key,value in data['sourceDates'].items() if value)
    story += [p(f"JY CONFIDENTIAL　报告时点 {data['asOf']}　数据范围 {data['scope']}",tiny),p('来源：'+(source_text or '暂无已上传来源'),tiny),Spacer(1,12)]
    metrics=[('客户资产净额',money(a['totalUsd']),f"{fmt(a['accounts'])} 个账户"),
             ('注册 → 开户成功',percent(k['accountSuccessRate']),f"{fmt(k['opened'])} / {fmt(k['customers'])} 人"),
             ('历史定增已参与',money(historical['converted']['amount']),f"{fmt(historical['converted']['count'])} 人 · 已停售"),
             ('XMAX 总持仓',fmt(h['quantity']),f"{fmt(h['holders'])} 人 · TW 去重"),
             ('购买计划意向',money(products['priority']['signed']['amount']),f"{fmt(products['priority']['signed']['count'])} 人")]
    cards=[]
    for label,value,note in metrics:
        cards.append([p(label,tiny),Spacer(1,4),rich('<font name="Times-Roman" size="14">'+escape(value)+'</font>'),Spacer(1,4),p(note,tiny)])
    kpi=Table([cards],colWidths=[width/5]*5)
    kpi.setStyle(TableStyle([('LINEABOVE',(0,0),(-1,0),1.1,ink),('LINEBELOW',(0,0),(-1,0),.5,rule),
                            ('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),10),('BOTTOMPADDING',(0,0),(-1,-1),10)]))
    story.append(kpi)
    story += section('01','ASSET COMPOSITION','客户资产分层','账户数量与资产贡献并列；零资产和负资产均保留。')
    asset_table=[[p(v,tiny) for v in ['资产分层','账户数','占比','资产 USD','资产占比']]]
    for b in a['buckets']:
        asset_table.append([p(b['label']),p(fmt(b['count']),right),p(percent(b['share']),right),p(fmt(b['amount'],2),right),p(percent(b['assetShare']),right)])
    asset_table.append([p('合计'),p(fmt(a['accounts']),right),p('100.00%' if a['accounts'] else '—',right),p(fmt(a['totalUsd'],2),right),p('100.00%' if a['totalUsd'] else '—',right)])
    # Keep the chart in its own right-hand column.  This gives the section
    # heading a full line above the artwork and keeps the data table readable.
    layout=Table([[table(asset_table,[94,35,48,87,51]), Donut()]],colWidths=[351,164])
    layout.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0)]))
    story.append(layout)
    story += [Spacer(1,6),p(f"正资产合计 {money(a.get('positiveUsd'))} · 净资产 {money(a['totalUsd'])} · 前十大客户按 TW 合并后占净资产 {percent(a.get('top10Share'))}",tiny)]
    story += section('02','CONVERSION FUNNEL','从客户进入到实际参与','注册与开户按人数；入金按正资产账户数，各自注明分母。')
    funnel=[]
    for label,value,total,note in [('注册开户',k['customers'],k['customers'],'客户名单 / 注册口径'),
                                  ('开户成功',k['opened'],k['customers'],'资产数值记录按 TW 去重'),
                                  ('资产入金',a['fundedAccounts'],a['accounts'],'占资产明细账户')]:
        funnel.append([[p(label),p(note,tiny)],Progress(value,total),[p(fmt(value),right),p(ratio(value,total),tiny)]])
    story.append(table(funnel,[126,270,119],False))
    story.append(PageBreak())
    story += section('03','PRODUCT CONVERSION','在售产品转化','意向金额和实际成交分别统计；待转化采用待办客户的意向金额。')
    for board,label in [('priority','购买计划'),('block','大宗交易')]:
        product=products[board];signed=product['signed'];converted=product['converted'];pending=product['pending'];dropped=product['dropped']
        block=[p(label,heading),p(f"意向 {fmt(signed['count'])} 人 / {money(signed['amount'])}　已完成 {fmt(converted['count'])} 人 / {money(converted['amount'])}"),
               Spacer(1,5),Progress(product.get('convertedIntentAmount',0),signed['amount']),
               p(f"待转化 {fmt(pending['count'])} 人 / {money(pending['amount'])}　已放弃 {fmt(dropped['count'])} 人 / {money(dropped['amount'])}",tiny),Spacer(1,16)]
        if product.get('historicalWorkflowUnavailable'):block.insert(-1,p('该时点成交状态待核对，后续修改的实时记录无法完整还原。',tiny))
        story.append(KeepTogether(block))
    story += section('04','CAPITAL BRIDGE','历史定增金额桥','已停售，仅作留档；不加回当前客户资产。')
    historical_rows=[[p(v,tiny) for v in ['历史产品','人数','金额 · USD','占签约金额']]]
    for label,key in [('签约总额','signed'),('已参与','converted'),('放弃','dropped'),('待转化','pending')]:
        item=historical[key]
        historical_rows.append([p(label),p(fmt(item['count']),right),p(money(item['amount']),right),p(ratio(item['amount'],historical['signed']['amount']),right)])
    story.append(table(historical_rows,[150,80,165,120]))
    story += section('05','XMAX POSITION','持仓结构与产品信号','分类人数可交叉；总持仓人数按 TW 去重。')
    holdings=[[p(v,tiny) for v in ['持仓来源','持仓人数','持仓股数']]]
    for label,key in [('二级买入','secondary'),('大宗交易','block'),('购买计划','priority')]:
        holdings.append([p(label),p(fmt(h[key+'Holders']),right),p(fmt(h[key+'Quantity']),right)])
    holdings.append([p('总持仓 · TW 去重'),p(fmt(h['holders']),right),p(fmt(h['quantity']),right)])
    story.append(table(holdings,[195,140,180]))
    story += [Spacer(1,7),p(f"持仓市值 {money(h['marketValue'])}　"+(f"按 ${fmt(h['price'],4)} / 股估算，非实时报价。" if h['price'] is not None else '来源未提供估值单价，市值留空。'),tiny)]
    story.append(PageBreak())
    story += section('06','HISTORICAL TREND','历史数据与经营轨迹','日期按真实时间间隔绘制；仅使用已上传快照，不补造增长数据。')
    trends=Table([[Trend('opened','开户成功人数',palette[0]),Trend('assetAum','客户资产净额 · USD',palette[1])],
                  [Trend('secondaryQuantity','二级持仓 · 股',palette[2]),Trend('priorityQuantity','购买计划持仓 · 股',gold)]],colWidths=[257,258],rowHeights=[164,164])
    trends.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),9)]))
    story.append(trends)
    story += section('07','DATA ASSURANCE','数据核对与报告修订','人工修订按本报告日期保存，原始来源数据保持独立。')
    reviews=[r for r in data['checks'] if r['status']!='pass']
    story.append(p(f"{len(reviews)} 项待核对 · {len(data['adjustments'])} 项人工修订" if reviews or data['adjustments'] else '已通过现有勾稽检查',body))
    for check in data['checks']:
        story.append(p(('通过 · ' if check['status']=='pass' else '待核对 · ')+check['label']+
                       (f"；差额 {fmt(check['difference'],2)}" if check.get('difference') and check['status']!='pass' else ''),tiny))
    for adjustment in data['adjustments']:
        label=next((o['label'] for o in data['correctionOptions'] if o['key']==adjustment['metric']),adjustment['metric'])
        story += [Spacer(1,5),p(f"人工修订：{label} = {fmt(adjustment['value'],4)} · {adjustment['author_name']} · {adjustment['created_at'][:10]}"),
                  p(f"依据：{adjustment['source']}；原因：{adjustment['reason']}",tiny)]
    story += [Spacer(1,10),p('统计口径',body)] + [p(note,tiny) for note in data['notes']]
    def footer(canvas,doc):
        canvas.setStrokeColor(rule);canvas.setLineWidth(.5);canvas.line(40,29,555,29)
        canvas.setFillColor(muted);canvas.setFont('Helvetica',7);canvas.drawString(40,17,'JY Customer Operations / CONFIDENTIAL')
        canvas.drawRightString(555,17,f"{data['asOf']}   |   {doc.page}")
    doc.build(story,onFirstPage=footer,onLaterPages=footer,canvasmaker=Canvas)
    return stream.getvalue()
