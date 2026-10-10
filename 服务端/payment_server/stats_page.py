"""仅公开汇总数字，不包含设备、令牌或订单明细。"""

STATS_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>青柠视频下载 · 使用统计</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f4f7f5;color:#173128;font-family:system-ui,"Microsoft YaHei",sans-serif}
main{max-width:1050px;margin:72px auto;padding:0 24px}.top{display:flex;justify-content:space-between;align-items:center;gap:20px}
.brand{color:#367d50;font-size:14px;font-weight:600}h1{font-size:32px;letter-spacing:-1px;margin:12px 0}
.hint,footer{color:#68776e;font-size:14px;line-height:1.8}button{border:1px solid #d4e2d7;background:white;color:#215a37;padding:11px 22px;border-radius:10px;font-size:14px;cursor:pointer}
button:disabled{opacity:.55;cursor:wait}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:18px;margin:32px 0}
.card{background:white;border:1px solid #e0e9e2;border-radius:16px;padding:26px 24px;box-shadow:0 5px 20px #163b2110}
.label{font-size:15px;color:#52665a}.number{font-size:46px;font-weight:650;margin:15px 0 12px;font-variant-numeric:tabular-nums}
.period{font-size:12px;color:#839087}#message{min-height:25px;color:#8c4d32;font-size:14px}
footer{border-top:1px solid #dfe8e1;padding-top:20px;margin-top:28px}
@media(max-width:800px){.grid{grid-template-columns:repeat(2,1fr)}main{margin-top:40px}}
@media(max-width:430px){.grid{gap:12px}.card{padding:20px 16px}.number{font-size:36px}h1{font-size:26px}}
</style></head><body><main>
<div class="top"><div><div class="brand">青柠视频下载</div><h1>使用统计</h1><div class="hint">按电脑 UUID 去重，统计最近启动过软件的用户。</div></div><button id="refresh">刷新</button></div>
<div class="grid">
<div class="card"><div class="label">今日活跃</div><div class="number" id="today_active">—</div><div class="period">今天 00:00 至现在</div></div>
<div class="card"><div class="label">近 7 天活跃</div><div class="number" id="active_7d">—</div><div class="period">今天及前 6 天</div></div>
<div class="card"><div class="label">近 30 天活跃</div><div class="number" id="active_30d">—</div><div class="period">今天及前 29 天</div></div>
<div class="card"><div class="label">累计用户数</div><div class="number" id="total_users">—</div><div class="period">累计登记的用户</div></div>
</div><div id="message" role="status" aria-live="polite"></div><div class="hint" id="updated">正在获取统计数据…</div>
<footer>时间范围按北京时间计算。同一电脑在一个统计范围内只计一次。活跃表示启动过软件，不表示当前在线。</footer>
</main><script>
const button=document.getElementById('refresh');
async function refresh(){
 button.disabled=true;document.getElementById('message').textContent='';
 try{
  const response=await fetch('/api/pc/stats',{cache:'no-store'});
  if(!response.ok)throw new Error('request failed');
  const data=await response.json();
  const keys=['today_active','active_7d','active_30d','total_users'];
  if(keys.some(key=>!Number.isSafeInteger(data[key])||data[key]<0)||!Number.isFinite(Date.parse(data.as_of)))throw new Error('invalid data');
  for(const key of keys)document.getElementById(key).textContent=data[key].toLocaleString('zh-CN');
  document.getElementById('updated').textContent='更新于 '+new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',dateStyle:'medium',timeStyle:'medium',hour12:false}).format(new Date(data.as_of))+'（北京时间）';
 }catch(error){document.getElementById('message').textContent='暂时无法获取数据，请稍后点击刷新。';}
 finally{button.disabled=false;}
}
button.addEventListener('click',refresh);refresh();
</script></body></html>"""
