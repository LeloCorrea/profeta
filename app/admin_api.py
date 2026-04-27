"""Admin dashboard — observability layer (read-only).

Routes:
  GET /admin                  → HTML dashboard (always served)
  GET /admin/api/overview     → JSON data (requires ?secret=ADMIN_SECRET)

Set ADMIN_SECRET in the environment to enable. Without it the JSON
endpoint returns 403 and the HTML page shows an access-denied message.
"""

import json
import os
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import distinct, func, select

try:
    from zoneinfo import ZoneInfo as _ZI
    _TZ_SP = _ZI("America/Sao_Paulo")
except Exception:
    _TZ_SP = timezone(timedelta(hours=-3))

from app.db import SessionLocal
from app.jobs import LOCK_FILE, LOG_FILE, get_users_missing_delivery
from app.models import Payment, Subscription, User, VerseHistory
from app.subscription_service import get_admin_stats

router = APIRouter(prefix="/admin", tags=["admin"])
_SECRET = os.getenv("ADMIN_SECRET", "")


# ── Timezone helpers (independent copy — no coupling to jobs internals) ────────

def _today_sp() -> date:
    return datetime.now(_TZ_SP).date()


def _sp_day_utc_range(d: date) -> tuple[datetime, datetime]:
    s = datetime(d.year, d.month, d.day, tzinfo=_TZ_SP)
    e = s + timedelta(days=1)
    return (
        s.astimezone(timezone.utc).replace(tzinfo=None),
        e.astimezone(timezone.utc).replace(tzinfo=None),
    )


# ── Lock status (sync, no DB) ──────────────────────────────────────────────────

def _lock_status() -> dict:
    exists = LOCK_FILE.exists()
    pid: Optional[int] = None
    alive: Optional[bool] = None
    stale = False
    if exists:
        try:
            raw = LOCK_FILE.read_text().strip()
            pid = int(raw) if raw.isdigit() else None
        except Exception:
            pass
        if pid is not None:
            try:
                os.kill(pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False
                stale = True
            except PermissionError:
                alive = True
            except OSError:
                alive = False
    return {"exists": exists, "pid": pid, "pid_alive": alive, "stale": stale}


# ── Log parsing (sync) ─────────────────────────────────────────────────────────

_LOG_RE = re.compile(
    r"^(?P<ts>[\d\-]+ [\d:,]+)\s*\|\s*(?P<lvl>\w+)\s*\|\s*(?P<body>.+)$"
)


def _tail(n: int = 1000) -> list[dict]:
    if not LOG_FILE.exists():
        return []
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except Exception:
        return []
    out = []
    for line in lines:
        m = _LOG_RE.match(line.strip())
        if not m:
            continue
        try:
            payload = json.loads(m.group("body"))
        except Exception:
            payload = {"raw": m.group("body")}
        out.append({"ts": m.group("ts").strip(), "lvl": m.group("lvl"), "payload": payload})
    return out


def _find_last(entries: list[dict], event: str) -> Optional[dict]:
    for e in reversed(entries):
        if e["payload"].get("event") == event:
            return e
    return None


def _recent_errors(entries: list[dict], limit: int = 8) -> list[dict]:
    out = []
    for e in reversed(entries):
        if e["lvl"] in ("ERROR", "CRITICAL"):
            out.append({
                "ts": e["ts"],
                "event": e["payload"].get("event", "—"),
                "detail": str(e["payload"])[:200],
            })
            if len(out) >= limit:
                break
    return list(reversed(out))


def _errors_last_24h(entries: list[dict]) -> int:
    cutoff = datetime.utcnow() - timedelta(hours=24)
    count = 0
    for e in entries:
        if e["lvl"] not in ("ERROR", "CRITICAL"):
            continue
        try:
            ts = datetime.strptime(e["ts"][:19], "%Y-%m-%d %H:%M:%S")
            if ts >= cutoff:
                count += 1
        except Exception:
            count += 1
    return count


# ── DB queries ─────────────────────────────────────────────────────────────────

async def _delivery_today() -> tuple[int, int]:
    today = _today_sp()
    start, end = _sp_day_utc_range(today)
    now = datetime.utcnow()
    async with SessionLocal() as db:
        active = (await db.execute(
            select(func.count()).select_from(User)
            .join(Subscription, Subscription.user_id == User.id)
            .where(Subscription.status == "active")
            .where(Subscription.paid_until > now)
        )).scalar_one() or 0
        delivered = (await db.execute(
            select(func.count(distinct(VerseHistory.telegram_user_id)))
            .where(VerseHistory.created_at >= start)
            .where(VerseHistory.created_at < end)
        )).scalar_one() or 0
    return active, delivered


async def _delivery_history(days: int = 7) -> list[dict]:
    today = _today_sp()
    start, _ = _sp_day_utc_range(today - timedelta(days=days - 1))
    async with SessionLocal() as db:
        rows = (await db.execute(
            select(VerseHistory.telegram_user_id, VerseHistory.created_at)
            .where(VerseHistory.created_at >= start)
        )).all()
    by_day: dict[date, set] = defaultdict(set)
    for uid, ts in rows:
        sp_day = ts.replace(tzinfo=timezone.utc).astimezone(_TZ_SP).date()
        by_day[sp_day].add(uid)
    return [
        {
            "date": (today - timedelta(days=i)).isoformat(),
            "delivered": len(by_day.get(today - timedelta(days=i), set())),
        }
        for i in range(days)
    ]


async def _recent_payments(limit: int = 8) -> list[dict]:
    async with SessionLocal() as db:
        ps = (await db.execute(
            select(Payment).order_by(Payment.created_at.desc()).limit(limit)
        )).scalars().all()
    return [
        {
            "id": p.provider_payment_id,
            "amount": p.amount or "—",
            "status": p.status or "—",
            "at": p.created_at.strftime("%d/%m %H:%M") if p.created_at else "—",
        }
        for p in ps
    ]


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/api/overview")
async def overview(secret: Optional[str] = Query(default=None)):
    if not _SECRET or secret != _SECRET:
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    lock = _lock_status()
    entries = _tail()
    job_ev = _find_last(entries, "daily_job_finished")
    retry_ev = _find_last(entries, "retry_missing_finished")
    errors = _recent_errors(entries)
    errors_24h = _errors_last_24h(entries)

    active, delivered = await _delivery_today()
    missing_ids = await get_users_missing_delivery()
    hist = await _delivery_history()
    subs = await get_admin_stats()
    pmts = await _recent_payments()

    jp = job_ev["payload"] if job_ev else {}
    log_total = jp.get("total", 0)
    log_sent = jp.get("sent", 0)
    log_recovered = jp.get("retry_recovered", 0)
    log_failed = jp.get("failed", 0)

    if not job_ev:
        job_status = "not_run" if delivered == 0 else "unknown"
    elif log_failed == 0:
        job_status = "success"
    elif log_sent == 0:
        job_status = "failed"
    else:
        job_status = "partial"

    rp = retry_ev["payload"] if retry_ev else {}

    return JSONResponse({
        "as_of": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "today_sp": _today_sp().isoformat(),
        "job": {
            "last_run_at": job_ev["ts"] if job_ev else None,
            "status": job_status,
            "active_subscribers": active,
            "delivered_today": delivered,
            "missing_today": len(missing_ids),
            "log_total": log_total,
            "log_sent": log_sent,
            "log_retry_recovered": log_recovered,
            "log_failed": log_failed,
        },
        "missing_ids": missing_ids,
        "retry": {
            "last_at": retry_ev["ts"] if retry_ev else None,
            "total": rp.get("total", 0),
            "sent": rp.get("sent", 0),
            "failed": rp.get("failed", 0),
        },
        "system": lock,
        "history": hist,
        "subscriptions": subs,
        "recent_payments": pmts,
        "errors_last_24h": errors_24h,
        "recent_errors": errors,
    })


@router.get("", response_class=HTMLResponse)
async def dashboard():
    return HTMLResponse(_HTML)


# ── HTML ───────────────────────────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Profeta · Dashboard</title>
<!-- v2: banner + delivery_rate + relTime -->
<style>
:root{--bg:#0f1117;--surf:#1a1d27;--border:#2a2d3d;--text:#e2e8f0;--muted:#64748b;
  --green:#22c55e;--yellow:#eab308;--red:#ef4444;--blue:#3b82f6;
  --font:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:var(--font);font-size:14px;line-height:1.5}
.hdr{padding:14px 24px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:12px}
.hdr h1{font-size:15px;font-weight:700;letter-spacing:.4px}
.hdr .meta{margin-left:auto;color:var(--muted);font-size:12px;text-align:right}
.main{padding:20px 24px;max-width:1180px;margin:0 auto;display:grid;gap:16px}
.sec{background:var(--surf);border:1px solid var(--border);border-radius:10px;overflow:hidden}
.sh{padding:10px 14px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.st{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:var(--muted)}
.sb{padding:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:10px}
.kpi{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:10px 12px}
.kl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px}
.kv{font-size:22px;font-weight:700;line-height:1}
.kv.g{color:var(--green)}.kv.r{color:var(--red)}.kv.y{color:var(--yellow)}.kv.b{color:var(--blue)}
.badge{display:inline-flex;align-items:center;padding:2px 9px;border-radius:20px;font-size:11px;font-weight:700}
.badge.success{background:rgba(34,197,94,.15);color:var(--green)}
.badge.partial{background:rgba(234,179,8,.15);color:var(--yellow)}
.badge.failed{background:rgba(239,68,68,.15);color:var(--red)}
.badge.not_run,.badge.unknown{background:rgba(100,116,139,.15);color:var(--muted)}
.badge.ok{background:rgba(34,197,94,.15);color:var(--green)}
.badge.warn{background:rgba(234,179,8,.15);color:var(--yellow)}
.badge.bad{background:rgba(239,68,68,.15);color:var(--red)}
.alert{padding:10px 14px;border-radius:7px;font-size:13px}
.alert.ok{background:rgba(34,197,94,.08);border:1px solid rgba(34,197,94,.2);color:var(--green)}
.alert.warn{background:rgba(234,179,8,.1);border:1px solid rgba(234,179,8,.25);color:var(--yellow)}
.alert.err{background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.25);color:var(--red)}
.alert.muted{background:rgba(100,116,139,.08);border:1px solid rgba(100,116,139,.2);color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--muted);font-weight:600;font-size:11px;text-transform:uppercase;
  letter-spacing:.5px;padding:6px 10px;border-bottom:1px solid var(--border)}
td{padding:7px 10px;border-bottom:1px solid rgba(255,255,255,.04)}
tr:last-child td{border-bottom:none}
.mono{font-family:monospace;font-size:12px}
.dim{color:var(--muted)}
.ids{font-family:monospace;font-size:12px;background:var(--bg);border:1px solid var(--border);
  border-radius:6px;padding:10px;line-height:1.9;margin-top:8px;word-break:break-all}
.ids span{background:rgba(239,68,68,.15);color:var(--red);padding:1px 6px;border-radius:3px;margin:2px;display:inline-block}
.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:640px){.two{grid-template-columns:1fr}}
.dot{width:7px;height:7px;border-radius:50%;display:inline-block;margin-right:4px}
.dot.g{background:var(--green);box-shadow:0 0 5px var(--green)}
.dot.r{background:var(--red);box-shadow:0 0 5px var(--red)}
.dot.y{background:var(--yellow);box-shadow:0 0 5px var(--yellow)}
.dot.grey{background:var(--muted)}
.bnr{border-radius:10px;padding:16px 20px;display:flex;align-items:flex-start;gap:16px;border-left:4px solid}
.bnr.ok{background:rgba(34,197,94,.07);border-color:var(--green)}
.bnr.warning{background:rgba(234,179,8,.07);border-color:var(--yellow)}
.bnr.critical{background:rgba(239,68,68,.07);border-color:var(--red);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{border-left-color:var(--red)}50%{border-left-color:rgba(239,68,68,.3)}}
.bnr-icon{font-size:26px;line-height:1;flex-shrink:0;margin-top:2px}
.bnr-body{flex:1;min-width:0}
.bnr-title{font-size:16px;font-weight:700;line-height:1.3}
.bnr-title.ok{color:var(--green)}.bnr-title.warning{color:var(--yellow)}.bnr-title.critical{color:var(--red)}
.bnr-sub{font-size:12px;color:var(--muted);margin-top:5px;line-height:1.6}
.bnr-meta{text-align:right;font-size:11px;color:var(--muted);flex-shrink:0;padding-top:2px;line-height:1.7}
</style>
</head>
<body>
<div class="hdr">
  <h1>⚡ Profeta · Dashboard</h1>
  <div class="meta">
    <div id="as-of">—</div>
    <div id="ticker" style="font-size:11px"></div>
  </div>
</div>
<div class="main" id="main"><div style="padding:40px;text-align:center;color:var(--muted)">Carregando…</div></div>

<script>
const qs = new URLSearchParams(location.search);
const SECRET = qs.get('secret') || '';
let countdown = 30, timer;

function sysStatus(missing, retryLastAt, retryFailed) {
  if (missing === 0) return 'ok';
  if (retryLastAt && retryFailed > 0) return 'critical';
  return 'warning';
}
function delivRate(delivered, active) {
  if (!active) return {pct: '—', cls: ''};
  const r = delivered / active;
  return {pct: (r * 100).toFixed(1) + '%', cls: r >= 1 ? 'g' : r >= 0.9 ? 'y' : 'r'};
}
function relTime(ts) {
  if (!ts) return null;
  try {
    const d = new Date(ts.replace(' ', 'T').replace(',', '.') + 'Z');
    const m = Math.round((Date.now() - d) / 60000);
    if (m < 1) return 'agora mesmo';
    if (m < 60) return `há ${m}min`;
    const h = Math.floor(m / 60), rm = m % 60;
    if (h < 24) return rm > 0 ? `há ${h}h ${rm}min` : `há ${h}h`;
    return `há ${Math.floor(h / 24)}d`;
  } catch (e) { return null; }
}

const BADGE_LABEL = {
  success:'✓ Sucesso', partial:'⚠ Parcial', failed:'✗ Falha',
  not_run:'— Não rodou', unknown:'? Desconhecido',
  ok:'✓ Ok', warn:'⚠ Atenção', bad:'✗ Problema'
};

function badge(cls, lbl) {
  return `<span class="badge ${cls}">${lbl || BADGE_LABEL[cls] || cls}</span>`;
}
function kpi(label, value, cls='') {
  return `<div class="kpi"><div class="kl">${label}</div><div class="kv ${cls}">${value}</div></div>`;
}
function dot(cls) { return `<span class="dot ${cls}"></span>`; }
function alert(cls, msg) { return `<div class="alert ${cls}">${msg}</div>`; }

function render(d) {
  const j = d.job, s = d.system, r = d.retry;
  const status = sysStatus(j.missing_today, r.last_at, r.failed);
  const dr = delivRate(j.delivered_today, j.active_subscribers);
  const rt = relTime(j.last_run_at);

  document.getElementById('as-of').textContent =
    'Atualizado ' + d.as_of.replace('T',' ').replace('Z',' UTC') + ' · Hoje SP: ' + d.today_sp;

  // ── Status banner ──────────────────────────────────────────────────────────
  const BNR = {
    ok:       {icon: '✓', title: 'Sistema saudável'},
    warning:  {icon: '⚠', title: r.last_at ? `${j.missing_today} usuário(s) ainda sem versículo hoje` : `${j.missing_today} usuário(s) aguardando retry automático`},
    critical: {icon: '✗', title: `${j.missing_today} usuário(s) não receberam mesmo após retry`},
  };
  const BNR_SUB = {
    ok:       `Taxa de entrega: <strong>${dr.pct}</strong> (${j.delivered_today} de ${j.active_subscribers}) · ${d.errors_last_24h} erro(s) nas últimas 24h`,
    warning:  `Taxa de entrega: <strong>${dr.pct}</strong> · Retry automático: ${r.last_at ? 'já executado' : 'ainda não executado hoje'} · ${d.errors_last_24h} erro(s) nas últimas 24h`,
    critical: `Taxa de entrega: <strong>${dr.pct}</strong> · Retry executado — ${r.sent} recuperado(s), <strong>${r.failed} ainda falhando</strong> · ${d.errors_last_24h} erro(s) nas últimas 24h`,
  };
  const bannerSection = `
    <div class="bnr ${status}">
      <div class="bnr-icon">${BNR[status].icon}</div>
      <div class="bnr-body">
        <div class="bnr-title ${status}">${BNR[status].title}</div>
        <div class="bnr-sub">${BNR_SUB[status]}</div>
      </div>
      ${rt ? `<div class="bnr-meta">Último job<br><strong>${rt}</strong></div>` : ''}
    </div>`;

  // ── Job status ─────────────────────────────────────────────────────────────
  const jobSection = `
    <div class="sec">
      <div class="sh">
        <span class="st">Job Hoje</span>
        ${badge(j.status)}
        ${rt ? `<span class="dim" style="font-size:11px;margin-left:auto">${rt}</span>` : ''}
      </div>
      <div class="sb">
        <div class="grid">
          ${kpi('Ativos', j.active_subscribers, 'b')}
          ${kpi('Entregues', j.delivered_today, j.delivered_today >= j.active_subscribers && j.active_subscribers > 0 ? 'g' : j.delivered_today > 0 ? 'y' : 'r')}
          ${kpi('Taxa Entrega', dr.pct, dr.cls)}
          ${kpi('Faltando', j.missing_today, j.missing_today === 0 ? 'g' : 'r')}
          ${kpi('Log Total', j.log_total)}
          ${kpi('Log Enviados', j.log_sent, j.log_failed === 0 && j.log_total > 0 ? 'g' : j.log_sent > 0 ? 'y' : '')}
          ${kpi('Retry Recuper.', j.log_retry_recovered, j.log_retry_recovered > 0 ? 'y' : '')}
          ${kpi('Falhas Finais', j.log_failed, j.log_failed === 0 ? 'g' : 'r')}
        </div>
      </div>
    </div>`;

  // ── Missing today ──────────────────────────────────────────────────────────
  let missingBody;
  if (d.missing_ids.length === 0) {
    missingBody = alert('ok', '✓ Todos os assinantes ativos receberam hoje.');
  } else {
    const ids = d.missing_ids.map(id => `<span>${id}</span>`).join('');
    missingBody = alert('warn', `⚠ ${d.missing_ids.length} usuário(s) ainda sem versículo hoje`) +
                  `<div class="ids">${ids}</div>`;
  }
  const missingSection = `
    <div class="sec">
      <div class="sh">
        <span class="st">Missing Today</span>
        ${badge(d.missing_ids.length === 0 ? 'ok' : 'warn',
                d.missing_ids.length === 0 ? 'Zero pendências' : `${d.missing_ids.length} pendente(s)`)}
      </div>
      <div class="sb">${missingBody}</div>
    </div>`;

  // ── Retry ─────────────────────────────────────────────────────────────────
  let retryBody;
  if (!r.last_at) {
    retryBody = alert('muted', 'Nenhuma execução de retry nos logs recentes.');
  } else {
    retryBody = `<div class="grid">
      ${kpi('Total', r.total)}
      ${kpi('Enviados', r.sent, r.failed === 0 ? 'g' : 'y')}
      ${kpi('Falhas', r.failed, r.failed === 0 ? 'g' : 'r')}
    </div>
    <div class="dim" style="font-size:11px;margin-top:8px">Última execução: ${r.last_at}</div>`;
  }
  const retrySection = `
    <div class="sec">
      <div class="sh"><span class="st">Retry Automático</span></div>
      <div class="sb">${retryBody}</div>
    </div>`;

  // ── System health ─────────────────────────────────────────────────────────
  let sysStatus;
  if (s.stale) sysStatus = badge('bad', `${dot('r')} Lock stale`);
  else if (s.exists && s.pid_alive) sysStatus = badge('warn', `${dot('y')} Job rodando`);
  else sysStatus = badge('ok', `${dot('g')} Saudável`);

  const sysSection = `
    <div class="sec">
      <div class="sh"><span class="st">Saúde do Sistema</span>${sysStatus}</div>
      <div class="sb">
        <div class="grid">
          ${kpi('Lock', s.exists ? 'Ativo' : 'Livre', s.exists ? (s.stale ? 'r' : 'y') : 'g')}
          ${kpi('PID', s.pid !== null ? s.pid : '—')}
          ${kpi('PID Vivo', s.pid_alive === null ? '—' : (s.pid_alive ? 'Sim' : 'Não'),
                s.pid_alive === true ? 'y' : s.pid_alive === false ? 'r' : '')}
          ${kpi('Stale', s.stale ? '⚠ Sim' : 'Não', s.stale ? 'r' : 'g')}
        </div>
      </div>
    </div>`;

  // ── History + Subscriptions ────────────────────────────────────────────────
  const histRows = d.history.map(h =>
    `<tr><td>${h.date}</td><td class="kv ${h.delivered > 0 ? 'g' : 'dim'}" style="font-size:16px">${h.delivered}</td></tr>`
  ).join('');
  const sub = d.subscriptions;
  const twoSection = `
    <div class="two">
      <div class="sec">
        <div class="sh"><span class="st">Histórico — 7 dias</span></div>
        <div class="sb">
          <table>
            <thead><tr><th>Data (SP)</th><th>Entregues</th></tr></thead>
            <tbody>${histRows}</tbody>
          </table>
        </div>
      </div>
      <div class="sec">
        <div class="sh"><span class="st">Assinantes</span></div>
        <div class="sb">
          <div class="grid">
            ${kpi('Total Usuários', sub.total_users)}
            ${kpi('Assinaturas Ativas', sub.active_subscriptions, 'g')}
            ${kpi('Expirando em 7d', sub.expiring_7_days, sub.expiring_7_days > 0 ? 'y' : '')}
          </div>
        </div>
      </div>
    </div>`;

  // ── Payments ──────────────────────────────────────────────────────────────
  const payRows = d.recent_payments.length === 0
    ? '<tr><td colspan="4" class="dim">Nenhum pagamento encontrado.</td></tr>'
    : d.recent_payments.map(p =>
        `<tr><td class="mono">${p.id}</td><td>R$ ${p.amount}</td>
         <td>${p.status}</td><td class="dim">${p.at}</td></tr>`
      ).join('');
  const paySection = `
    <div class="sec">
      <div class="sh"><span class="st">Pagamentos Recentes</span></div>
      <div class="sb">
        <table>
          <thead><tr><th>ID</th><th>Valor</th><th>Status</th><th>Data</th></tr></thead>
          <tbody>${payRows}</tbody>
        </table>
      </div>
    </div>`;

  // ── Errors ────────────────────────────────────────────────────────────────
  let errBody;
  if (d.recent_errors.length === 0) {
    errBody = alert('ok', 'Nenhum erro recente nos logs.');
  } else {
    const rows = d.recent_errors.map(e =>
      `<tr><td class="mono dim">${e.ts}</td><td>${e.event}</td>
       <td class="mono dim" style="max-width:360px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
           title="${e.detail.replace(/"/g,'&quot;')}">${e.detail}</td></tr>`
    ).join('');
    errBody = `<table>
      <thead><tr><th>Horário</th><th>Evento</th><th>Detalhe</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  }
  const errSection = `
    <div class="sec">
      <div class="sh"><span class="st">Erros Recentes</span></div>
      <div class="sb">${errBody}</div>
    </div>`;

  document.getElementById('main').innerHTML =
    bannerSection + jobSection + missingSection + retrySection + sysSection +
    twoSection + paySection + errSection;
}

async function load() {
  try {
    const res = await fetch('/admin/api/overview?secret=' + encodeURIComponent(SECRET));
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      document.getElementById('main').innerHTML =
        `<div style="padding:40px;text-align:center;color:var(--red)">
          Erro ${res.status}: ${err.error || 'Acesso negado'}<br>
          <small style="color:var(--muted)">Verifique o parâmetro ?secret= na URL</small>
         </div>`;
      return;
    }
    render(await res.json());
  } catch (e) {
    document.getElementById('main').innerHTML =
      `<div style="padding:40px;text-align:center;color:var(--red)">Falha ao carregar: ${e.message}</div>`;
  }
}

function tick() {
  countdown--;
  document.getElementById('ticker').textContent = `refresh em ${countdown}s`;
  if (countdown <= 0) { load(); countdown = 30; }
}

load().then(() => { timer = setInterval(tick, 1000); });
</script>
</body>
</html>"""
