"""HTML template for the web admin dashboard."""

ADMIN_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Profeta — Admin</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}
    header{background:#1e293b;padding:14px 24px;border-bottom:1px solid #334155;display:flex;align-items:center;gap:10px}
    header svg{flex-shrink:0}
    header h1{font-size:17px;font-weight:600;color:#f1f5f9}
    nav{background:#1e293b;border-bottom:1px solid #334155;padding:0 24px;display:flex;gap:2px}
    nav a{padding:11px 16px;text-decoration:none;color:#94a3b8;font-size:13px;border-bottom:2px solid transparent;cursor:pointer;transition:color .15s}
    nav a.active{color:#38bdf8;border-bottom-color:#38bdf8}
    nav a:hover{color:#e2e8f0}
    main{padding:24px;max-width:1280px;margin:0 auto}
    .kpi-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:14px;margin-bottom:32px}
    .kpi-card{background:#1e293b;border:1px solid #334155;border-radius:10px;padding:18px 20px}
    .kpi-label{font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px}
    .kpi-value{font-size:26px;font-weight:700;color:#f1f5f9;line-height:1.1}
    .kpi-sub{font-size:11px;color:#64748b;margin-top:6px}
    section{margin-bottom:40px}
    section h2{font-size:15px;font-weight:600;color:#cbd5e1;margin-bottom:14px;padding-bottom:10px;border-bottom:1px solid #1e293b}
    .table-wrap{overflow-x:auto;border-radius:8px;border:1px solid #334155}
    table{width:100%;border-collapse:collapse;font-size:13px}
    th{text-align:left;padding:10px 14px;background:#1e293b;color:#64748b;font-weight:500;text-transform:uppercase;font-size:11px;letter-spacing:.04em;white-space:nowrap}
    td{padding:10px 14px;border-top:1px solid #1e293b;color:#cbd5e1;vertical-align:middle}
    tr:hover td{background:#1e293b}
    .badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;white-space:nowrap}
    .badge-add{background:#064e3b;color:#34d399}
    .badge-consume{background:#450a0a;color:#f87171}
    .badge-refund{background:#172554;color:#60a5fa}
    .mono{font-family:ui-monospace,monospace;font-size:11px}
    .msg{color:#64748b;font-size:13px;padding:20px 0;text-align:center}
    .err{color:#f87171;font-size:13px;padding:20px 0;text-align:center}
    .refresh-btn{float:right;background:#1e293b;border:1px solid #334155;color:#94a3b8;padding:6px 14px;border-radius:6px;font-size:12px;cursor:pointer;transition:color .15s}
    .refresh-btn:hover{color:#e2e8f0}
  </style>
</head>
<body>
<header>
  <svg width="22" height="22" fill="none" stroke="#38bdf8" stroke-width="2" viewBox="0 0 24 24">
    <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/>
  </svg>
  <h1>Profeta &mdash; Admin</h1>
</header>
<nav>
  <a class="active">💰 Financeiro</a>
</nav>
<main>
  <div class="kpi-grid" id="kpi-grid"><div class="msg">Carregando...</div></div>

  <section>
    <h2>Transações de créditos <button class="refresh-btn" onclick="loadTransactions()">↻ Atualizar</button></h2>
    <div class="table-wrap">
      <div id="tx-table"><div class="msg">Carregando...</div></div>
    </div>
  </section>

  <section>
    <h2>Pagamentos <button class="refresh-btn" onclick="loadPayments()">↻ Atualizar</button></h2>
    <div class="table-wrap">
      <div id="pay-table"><div class="msg">Carregando...</div></div>
    </div>
  </section>
</main>
<script>
const SECRET = new URLSearchParams(location.search).get('secret') || '';

function brl(v){ return v==null ? '—' : 'R$ '+Number(v).toFixed(2).replace('.',','); }

function badge(type){
  const cls={add:'badge-add',consume:'badge-consume',refund:'badge-refund'};
  const lbl={add:'+ adicionado',consume:'− consumido',refund:'↺ estorno'};
  return `<span class="badge ${cls[type]||''}">${lbl[type]||type}</span>`;
}

async function api(path){
  const r=await fetch('/admin/api'+path+'?secret='+encodeURIComponent(SECRET));
  if(!r.ok) throw new Error('HTTP '+r.status);
  return r.json();
}

async function loadSummary(){
  const el=document.getElementById('kpi-grid');
  try{
    const d=await api('/finance');
    el.innerHTML=`
      <div class="kpi-card"><div class="kpi-label">Receita total (est.)</div><div class="kpi-value">${brl(d.total_revenue)}</div></div>
      <div class="kpi-card"><div class="kpi-label">Receita hoje (est.)</div><div class="kpi-value">${brl(d.revenue_today)}</div></div>
      <div class="kpi-card"><div class="kpi-label">Total de pagamentos</div><div class="kpi-value">${d.total_payments}</div></div>
      <div class="kpi-card"><div class="kpi-label">Créditos vendidos</div><div class="kpi-value">${d.credits_sold}</div><div class="kpi-sub">consumidos: ${d.credits_consumed}</div></div>
      <div class="kpi-card"><div class="kpi-label">Saldo em créditos</div><div class="kpi-value">${d.credits_balance_total}</div><div class="kpi-sub">${d.total_users_with_credits} usuário(s) com saldo</div></div>
    `;
  }catch(e){el.innerHTML=`<div class="err">Erro ao carregar KPIs: ${e.message}</div>`;}
}

async function loadTransactions(){
  const el=document.getElementById('tx-table');
  el.innerHTML='<div class="msg">Carregando...</div>';
  try{
    const rows=await api('/finance/transactions');
    if(!rows.length){el.innerHTML='<div class="msg">Sem transações registradas.</div>';return;}
    el.innerHTML=`<table>
      <thead><tr><th>Usuário</th><th>Tipo</th><th>Qtd</th><th>Referência</th><th>Data (UTC)</th></tr></thead>
      <tbody>${rows.map(r=>`<tr>
        <td class="mono">${r.user_id}</td>
        <td>${badge(r.type)}</td>
        <td>${r.amount>0?'+':''}${r.amount}</td>
        <td class="mono">${r.reference||'—'}</td>
        <td>${r.created_at}</td>
      </tr>`).join('')}</tbody>
    </table>`;
  }catch(e){el.innerHTML=`<div class="err">Erro: ${e.message}</div>`;}
}

async function loadPayments(){
  const el=document.getElementById('pay-table');
  el.innerHTML='<div class="msg">Carregando...</div>';
  try{
    const rows=await api('/finance/payments');
    if(!rows.length){el.innerHTML='<div class="msg">Sem pagamentos registrados.</div>';return;}
    el.innerHTML=`<table>
      <thead><tr><th>Payment ID</th><th>Customer ID</th><th>Valor</th><th>Status</th><th>Data (UTC)</th></tr></thead>
      <tbody>${rows.map(r=>`<tr>
        <td class="mono">${r.payment_id}</td>
        <td class="mono">${r.customer_id||'—'}</td>
        <td>${brl(r.value)}</td>
        <td>${r.status}</td>
        <td>${r.created_at}</td>
      </tr>`).join('')}</tbody>
    </table>`;
  }catch(e){el.innerHTML=`<div class="err">Erro: ${e.message}</div>`;}
}

loadSummary();
loadTransactions();
loadPayments();
</script>
</body>
</html>"""
