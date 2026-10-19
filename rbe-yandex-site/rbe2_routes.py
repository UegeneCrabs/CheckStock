"""Routes for the development-only independent operational report."""
import json
import threading
from datetime import UTC, date, datetime, timedelta

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from cost_snapshots import prices_for_day
from rbe2_calculation import calculate
from rbe2_service import read_snapshots
from rbe2_scheduler import load_account, plan


class Rbe2SyncRequest(BaseModel):
    date_from: date
    date_to: date
    store_slug: str | None = None


def install(app, site):
    def accounts(store=None):
        rows = site['load_connections']()
        if store and store not in {r['store_slug'] for r in rows}:
            raise HTTPException(422,'Выберите подключённый магазин')
        return [r for r in rows if not store or r['store_slug']==store]

    @app.get('/rbe2')
    def rbe2_page(request:Request):
        site['_require_user'](request)
        return FileResponse(site['ROOT']/'templates'/'rbe2.html')

    @app.get('/api/rbe2/report')
    def rbe2_report(request:Request,date_from:date,date_to:date,store_slug:str|None=None,campaign_id:int|None=None):
        user = site['_require_user'](request)
        if date_to<date_from or (date_to-date_from).days>366:
            raise HTTPException(422,'Выберите период от 1 до 367 дней')
        selected_accounts = accounts(store_slug)
        with site['connection']() as conn:
            snapshots = read_snapshots(conn,sorted({r['store_slug'] for r in selected_accounts}))
            campaigns = {int(c['id']):{'id':int(c['id']),'store_slug':s['store_slug'],
                        'business_id':s['business_id'],'name':c.get('domain',str(c['id'])),
                        'model':c.get('placementType','')} for s in snapshots for c in s['data'].get('campaigns',[])}
            if campaign_id is not None:
                if campaign_id not in campaigns:
                    raise HTTPException(422,'Сначала загрузите выбранную кампанию')
                owner = campaigns[campaign_id]
                selected_accounts = [a for a in selected_accounts if a['store_slug']==owner['store_slug']
                                     and int(a['business_id'])==owner['business_id']]
            result = calculate(snapshots,selected_accounts,date_from,date_to,
                lambda store,day:prices_for_day(conn,store,day),campaign_id=campaign_id)
        return {'ok':True,'mode':'operational','date_from':date_from.isoformat(),'date_to':date_to.isoformat(),
                **result,'can_sync':user['role']=='admin','sync_running':site['SYNC_LOCK'].locked(),
                'campaigns':list(campaigns.values()),'automatic':site['RBE2_AUTO_SYNC_ENABLED'],
                'last_loaded_at':max((s['fetched_at'] for s in snapshots),default=None)}

    def worker(run_id,start,end,selected_accounts):
        succeeded, failed = [], []
        try:
            with site['connection']() as conn:
                conn.execute("UPDATE rbe2_runs SET status='running' WHERE id=?",(run_id,))
            for account in selected_accounts:
                store = account['store_slug']
                outcome, problem = load_account(site,account,start,end)
                if outcome != 'failed':
                    succeeded.append(store)
                if outcome != 'completed':
                    failed.append({'store':store,'detail':problem})
            status = 'completed' if not failed else 'partial' if succeeded else 'failed'
            detail = json.dumps({'loaded':succeeded,'errors':failed},ensure_ascii=False)
        except Exception as error:
            status, detail = 'failed',str(error)[:500]
        finally:
            try:
                with site['connection']() as conn:
                    conn.execute('UPDATE rbe2_runs SET status=?,finished_at=?,detail=? WHERE id=?',
                                 (status,datetime.now(UTC).isoformat(),detail,run_id))
            finally:
                site['SYNC_LOCK'].release()

    @app.post('/api/rbe2/sync',status_code=202)
    def rbe2_sync(request:Request,payload:Rbe2SyncRequest):
        site['_require_admin'](request)
        if payload.date_to<payload.date_from or (payload.date_to-payload.date_from).days>6:
            raise HTTPException(422,'Загружайте RBE 2 периодами от 1 до 7 дней')
        if payload.date_from<site['_api_cutoff']():
            raise HTTPException(422,'Даты старше 90 дней не запрашиваются')
        if payload.date_to>datetime.now(site['MOSCOW']).date():
            raise HTTPException(422,'Будущие даты не загружаются')
        selected_accounts = accounts(payload.store_slug)
        if not selected_accounts:
            raise HTTPException(422,'Нет подключённых магазинов')
        if not site['SYNC_LOCK'].acquire(blocking=False):
            raise HTTPException(409,'Уже идёт загрузка. Дождитесь её завершения')
        try:
            with site['connection']() as conn:
                row = conn.execute('INSERT INTO rbe2_runs(started_at,status,detail) VALUES(?,?,?)',
                    (datetime.now(UTC).isoformat(),'queued','Загружаем операционные заказы, возвраты и полные начисления услуг'))
                run_id = row.lastrowid
            thread = threading.Thread(target=worker,args=(run_id,payload.date_from,payload.date_to,selected_accounts),daemon=True)
            thread.start()
        except Exception:
            site['SYNC_LOCK'].release()
            raise
        return {'ok':True,'run_id':run_id,'detail':'Загрузка RBE 2 запущена. Финансовый расчёт RBE не изменяется.'}

    @app.get('/api/rbe2/sync-status')
    def rbe2_sync_queue(request:Request):
        site['_require_user'](request)
        end = datetime.now(site['MOSCOW']).date() - timedelta(days=1)
        with site['connection']() as conn:
            jobs = plan(conn, [a['store_slug'] for a in accounts()], site['_api_cutoff'](), end,
                        datetime.now(UTC), retry_minutes=0,
                        lookback_days=site['RBE2_SYNC_LOOKBACK_DAYS'], limit=1000)
            run = conn.execute('SELECT status,detail FROM rbe2_runs ORDER BY id DESC LIMIT 1').fetchone()
        return {'ok':True,'automatic':site['RBE2_AUTO_SYNC_ENABLED'],
                'pending_jobs':len(jobs),'next_jobs':[
                    {'store':store,'date_from':start.isoformat(),'date_to':finish.isoformat()}
                    for store,start,finish in jobs[:10]],
                'latest_run':dict(run) if run else None}

    @app.get('/api/rbe2/sync/{run_id}')
    def rbe2_sync_status(request:Request,run_id:int):
        site['_require_user'](request)
        with site['connection']() as conn:
            row = conn.execute('SELECT * FROM rbe2_runs WHERE id=?',(run_id,)).fetchone()
        if not row:
            raise HTTPException(404,'Загрузка не найдена')
        result = dict(row)
        if result['detail'].startswith('{'):
            detail = json.loads(result['detail'])
            loaded = ', '.join(s.upper() for s in detail.get('loaded',[]))
            errors = '; '.join(f"{r['store'].upper()}: {r['detail']}" for r in detail.get('errors',[]))
            result['detail'] = (f'Загружены данные: {loaded}. ' if loaded else '') + (f'Не удалось загрузить: {errors}' if errors else '')
        return {'ok':True,**result}
