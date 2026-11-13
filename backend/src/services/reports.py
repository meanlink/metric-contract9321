import time, os, csv, io, hashlib, json
from fastapi import HTTPException
from src.models import Job, Report, Delivery
from src.config.settings import ARTIFACT_DIR
def create(db, data):
    j=db.get(Job,data.job_id)
    if not j or j.status!='completed' or not j.approved:
        raise HTTPException(409,'请先选择已完成且获批准的分析结果')
    r=Report(name=data.name, job_id=j.id, destination=data.destination, interval_seconds=data.interval_seconds, next_run=time.time()+data.delay_seconds, enabled=True)
    db.add(r); db.flush()
    return r
def deliver(db, report):
    j=db.get(Job,report.job_id)
    if not j or not j.approved: return
    out=io.StringIO()
    out.write('\ufeff')
    def safe(v):
        if isinstance(v,(int,float)) and not isinstance(v,bool): return v
        text=str(v)
        return "'"+text if text.startswith(('=','+','-','@','\t','\r')) else text
    writer=csv.writer(out)
    # \u9875\u9762\u4e0e\u5bfc\u51fa\u5171\u7528 job.result\uff1a\u540c\u4e00\u6279\u6e20\u9053\u3001\u81ea\u7136\u5468\u3001\u4ea4\u53c9\u4e0e\u6e90\u8d26\u9010\u884c\u8ffd\u6eaf\u7ed3\u679c\u3002
    tables=j.result.get('tables') or [j.result.get('table',{})]
    for index,table in enumerate(tables):
        if index: writer.writerow([])
        writer.writerow([safe(table.get('title') or '\u5bf9\u8d26\u660e\u7ec6')])
        if table.get('note'): writer.writerow([safe(table['note'])])
        cols=table.get('columns',[])
        writer.writerow([safe(c['label']) for c in cols])
        for row in table.get('rows',[]): writer.writerow([safe(row.get(c['key'],'')) for c in cols])
    checks=j.result.get('checks') or {}
    if checks:
        writer.writerow([])
        writer.writerow([safe('\u5bf9\u8d26\u6821\u9a8c')])
        writer.writerow([safe('\u603b\u5dee\u989d'),safe(checks.get('total_difference'))])
        writer.writerow([safe('\u660e\u7ec6\u8d21\u732e\u5408\u8ba1'),safe(checks.get('contribution_sum'))])
        writer.writerow([safe('\u8d21\u732e\u5408\u8ba1\u4e0e\u603b\u5dee\u989d\u4e00\u81f4'),safe('\u662f' if checks.get('contribution_consistent') else '\u5426')])
        writer.writerow([safe('\u91d1\u989d\u5dee\u989d\u5408\u8ba1\u4e0e\u603b\u5dee\u989d\u4e00\u81f4'),safe('\u662f' if checks.get('amount_difference_consistent') else '\u5426')])
        writer.writerow([safe('\u5355\u4f4d'),safe(checks.get('unit'))])
    if j.result.get('explanations'):
        writer.writerow([])
        writer.writerow([safe('\u8fb9\u754c\u89e3\u91ca')])
        for edge in j.result['explanations'].values():
            writer.writerow([safe(edge.get('explanation',''))])
    data=out.getvalue().encode('utf-8')
    delivery=Delivery(report_id=report.id, created_at=time.time(), checksum=hashlib.sha256(data).hexdigest(), filename='', destination=report.destination)
    db.add(delivery); db.flush()
    delivery.filename=f'report-{report.id}-delivery-{delivery.id}.csv'
    os.makedirs(ARTIFACT_DIR,exist_ok=True)
    with open(os.path.join(ARTIFACT_DIR,delivery.filename),'wb') as f:f.write(data)
    with open(os.path.join(ARTIFACT_DIR,delivery.filename+'.json'),'w') as f: json.dump({'job':j.name,'params':j.params,'result':j.result,'checksum':delivery.checksum},f,ensure_ascii=False,allow_nan=False)
    report.next_run=time.time()+report.interval_seconds
    return delivery
