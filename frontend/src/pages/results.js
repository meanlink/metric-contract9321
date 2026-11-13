import {jobs} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {chart} from '../components/common/chart.js';
import {table} from '../components/common/table.js';
import {status} from '../components/common/status.js';
import {esc,fmt,date,notify} from '../utils/dom.js';
function checksBlock(r){
 const c=r.checks;if(!c)return '';
 const ok=c.contribution_consistent&&c.amount_difference_consistent;
 const ratio=(c.unit||'').includes('/');
 return '<section class="panel reconciliation-check"><div class="section-title"><h2>可加性校验</h2><span class="'+(ok?'pass':'fail')+'">'+(ok?'明细汇总与总差额一致':'存在不一致，请勿下发')+'</span></div><div class="check-grid"><div><span>总差额</span><strong>'+esc(fmt(c.total_difference))+'<small>'+esc(c.unit)+'</small></strong></div><div><span>明细贡献合计</span><strong>'+esc(fmt(c.contribution_sum))+'<small>'+esc(c.unit)+'</small></strong></div>'+(ratio?'<div><span>总金额差额</span><strong>'+esc(fmt(c.total_amount_difference))+'</strong></div><div><span>金额差额合计</span><strong>'+esc(fmt(c.amount_difference_sum))+'</strong></div>':'')+'<div><span>展示舍入尾差</span><strong>'+esc(fmt(c.rounding_residual))+'</strong></div></div><p class="hint">'+(ratio?'单次访问口径下，各组自身的单次差额不可相加；可加的是金额差额和对总比率差额的贡献＝组金额差额÷总访问分母。':'金额口径下，金额差额即对总差额的贡献。')+' 展示值保留 4 位小数，尾差统一落在贡献绝对值最大的一行；页面与导出使用同一份计算结果的同一批明细。</p></section>';
}
function explanationBlock(r){
 const e=r.explanations||{};if(!e.zero_visits&&!e.negative_net)return '';
 const block=(title,item,cls)=>!item||!item.explanation?'':'<div class="edge-explanation '+cls+'"><h3>'+esc(title)+'<small>'+esc(item.count)+' 行</small></h3><p>'+esc(item.explanation)+'</p></div>';
 const rows=(e.zero_visits?.rows||[]).slice(0,5).concat(e.negative_net?.rows||[]).filter((v,i,a)=>a.indexOf(v)===i).slice(0,8);
 return '<section class="panel"><div class="section-title"><h2>边界记录解释</h2><span>零访问与负净收入不被静默截断</span></div>'+block('零访问记录',e.zero_visits,'zero')+block('负净收入记录',e.negative_net,'negative')+(rows.length?'<div class="edge-rows">'+rows.map(x=>'<div><strong>'+esc(x.day)+' · '+esc(x.channel)+'</strong><span>访问 '+esc(fmt(x.visits))+' · 统一口径 '+esc(fmt(x.computed_amount))+' · 源账 '+esc(fmt(x.source_amount))+'</span><small>'+esc(x.note)+'</small></div>').join('')+'</div>':'')+'</section>';
}
function tablesBlock(r){
 const tables=r.tables?.length?r.tables:(r.table?[r.table]:[]);
 return tables.map(t=>'<section class="panel"><div class="section-title"><h2>'+esc(t.title||'结果明细')+'</h2><span>'+(t.rows?.length||0)+' 行</span></div>'+(t.note?'<p class="hint">'+esc(t.note)+'</p>':'')+table(t.columns||[],t.rows||[])+'</section>').join('');
}
export async function results(id){
 workspace.jobs=await jobs.list();const j=id?await jobs.get(id):(workspace.jobs.find(j=>j.id===workspace.selectedJob)||workspace.jobs[0]);
 if(j&&!workspace.jobs.some(x=>x.id===j.id))workspace.jobs.push(j);
 if(!j)return heading('04 / REVIEW','结果复核','尚无分析结果。')+'<div class="empty">先到分析工作台，选择批次并提交一次计算。<a href="#/workbench">前往工作台 ↗</a></div>';
 workspace.selectedJob=j.id;const r=j.result||{};
 return heading('04 / REVIEW','结果复核与业务解释',j.name,'<button class="button" id="refresh-results">刷新结果 ↻</button>')+'<div class="result-selector"><label>分析记录<select id="select-job">'+workspace.jobs.map(x=>'<option value="'+x.id+'" '+(x.id===j.id?'selected':'')+'>'+esc(x.name)+' · '+esc(x.status)+'</option>').join('')+'</select></label>'+status(j.status)+'<span>'+date(j.created_at)+'</span><span class="approval">'+(j.approved?'已获业务批准':'待负责人复核')+'</span></div>'+(j.status==='completed'?'<section class="result-lead"><p class="eyebrow">COMPUTED FINDING</p><h2>'+esc(r.summary)+'</h2><div class="metrics">'+(r.metrics||[]).map(m=>'<div><span>'+esc(m.label)+'</span><strong>'+esc(fmt(m.value))+'<small>'+esc(m.unit||'')+'</small></strong></div>').join('')+'</div></section><section class="panel">'+chart(r.chart)+'</section>'+checksBlock(r)+'<div class="two-columns"><section class="panel"><div class="section-title"><h2>业务解释</h2><span>由计算结果生成</span></div><ol class="questions">'+(r.findings||[]).map(v=>'<li>'+esc(v)+'</li>').join('')+'</ol>'+(r.warnings||[]).map(v=>'<p class="warning">'+esc(v)+'</p>').join('')+'</section><section class="method-sheet compact"><p class="eyebrow">REPRODUCIBILITY</p><h3>计算口径与参数</h3><p>'+esc(r.method)+'</p><pre>'+esc(JSON.stringify(j.params,null,2))+'</pre>'+(session.user.role==='admin'&&!j.approved?'<button class="primary" id="approve-job">批准此业务结论</button>':'')+'<p class="hint">仅批准结果可进入定时分发档案；导出与页面使用同一批对账结果。</p></section></div>'+explanationBlock(r)+tablesBlock(r):'<section class="panel empty">'+(j.status==='failed'?'计算失败：'+esc(j.error)+'。请返回工作台调整参数或数据。':'后台正在执行计算。点击刷新结果查看最新进展。')+'<a href="#/workbench">返回工作台 ↗</a></section>');
}
export function bindResults(refresh){document.querySelector('#refresh-results')?.addEventListener('click',refresh);document.querySelector('#select-job')?.addEventListener('change',e=>{workspace.selectedJob=Number(e.target.value);location.hash='#/results/'+e.target.value;});document.querySelector('#approve-job')?.addEventListener('click',async()=>{try{await jobs.approve(workspace.selectedJob);notify('已批准，可创建分发计划');await refresh();}catch(e){notify(e.message,true);}});}
