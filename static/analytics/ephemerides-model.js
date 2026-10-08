(function(scope) {
    'use strict';
    const sum = values => { const numbers = values.filter(v => typeof v === 'number' && Number.isFinite(v)); return numbers.length ? numbers.reduce((a,b) => a+b,0) : null; };
    const ratio = (a,b) => a != null && b > 0 ? a/b*100 : null;
    const metricKey = row => JSON.stringify([row.store_slug, String(row.article).split(' / ')[0].trim()]);
    function unique(rows) {
        const result = new Map();
        rows.forEach(row => { const key = metricKey(row); if (!result.has(key)) result.set(key, []); result.get(key).push(row); });
        return [...result.values()];
    }
    function shared(rows, value) {
        const values = new Set(rows.map(value).filter(v => v != null));
        return values.size === 1 ? [...values][0] : null;
    }
    function summary(rows, period, key) {
        const value = row => period ? row[period]?.[key] : row[key];
        if (key === 'goal' || ['fbs','fbo'].includes(key)) return sum(rows.map(value));
        if (key === 'stock') return sum([summary(rows,null,'fbs'),summary(rows,null,'fbo'),summary(rows,null,'fromCustomer')]);
        if (['fact','turnover','impressions','fromCustomer'].includes(key)) return sum(unique(rows).map(group => shared(group,value)));
        const weights = {ctr:['clicks','impressions'], drr:['spend','boughtAmount'], roi:['profit','purchase']}[key];
        if (period && weights) {
            const pairs = unique(rows).map(group => weights.map(k => shared(group, row => row[period]?.weights?.[k]))).filter(p => p.every(v => v != null));
            const a=sum(pairs.map(p=>p[0])), b=sum(pairs.map(p=>p[1]));
            return key === 'drr' && a === 0 && b === 0 ? 0 : ratio(a,b);
        }
        return null;
    }
    function monday(iso) { const d=new Date(iso+'T12:00:00Z'); d.setUTCDate(d.getUTCDate()-(d.getUTCDay()+6)%7); return d.toISOString().slice(0,10); }
    function shift(iso, days) { const d=new Date(iso+'T12:00:00Z'); d.setUTCDate(d.getUTCDate()+days); return d.toISOString().slice(0,10); }
    const moscowDateFormat = new Intl.DateTimeFormat('en-CA', {timeZone:'Europe/Moscow',year:'numeric',month:'2-digit',day:'2-digit'});
    function moscowToday(now=new Date()) {
        const parts=Object.fromEntries(moscowDateFormat.formatToParts(now).map(part=>[part.type,part.value]));
        return `${parts.year}-${parts.month}-${parts.day}`;
    }
    function canEditComment(canEdit, kind, week, today=moscowToday()) {
        return !!canEdit && (kind!=='decision' || week===monday(today));
    }
    function weeklyCommentColumns(weeks, kind='decision', width=165) {
        return weeks.map(w=>({id:'week-'+w.start,key:'week-'+w.start,label:`W${w.number} · ${w.year}`,group:'history',width,type:'comment',week:w.start,kind,readOnly:kind==='decision'}));
    }
    function commentPresentation(item, canEdit, readOnly=false, today=moscowToday()) {
        const editable=!readOnly && canEditComment(canEdit,item.kind,item.week,today);
        const empty=!editable && !item.text && !item.meta && !item.dirty && !item.pending && !item.error;
        return {editable,empty};
    }
    // One state per logical field, shared by table and drawer. A response to an
    // older request must never erase text typed while that request was running.
    class Drafts {
        constructor(send, notify=()=>{}) { this.send=send; this.notify=notify; this.items=new Map(); }
        key(row, week, kind) { return JSON.stringify([row.store_slug,row.article,week,kind]); }
        get(row, week, kind) {
            const key=this.key(row,week,kind);
            if (!this.items.has(key)) {
                const saved=row.comments?.[week+':'+kind];
                this.items.set(key,{key,row,week,kind,text:saved?.text || '',saved:saved?.text || '',version:saved?.version || 0,meta:saved,dirty:false,error:null,pending:null,timer:null});
            }
            return this.items.get(key);
        }
        edit(item,text) { item.text=text; item.dirty=text!==item.saved || !!item.error; clearTimeout(item.timer); item.timer=setTimeout(()=>this.save(item),5000); this.notify(item); }
        async save(item) {
            clearTimeout(item.timer);
            if (item.pending) { await item.pending; if (item.error) return false; return this.save(item); }
            if (!item.dirty) return !item.error;
            if (item.error?.conflict) return false;
            const text=item.text;
            item.error=null;
            item.pending=(async()=>{
                try {
                    const saved=await this.send({store:item.row.store_slug,article:item.row.article,week:item.week,kind:item.kind,text,version:item.version});
                    item.saved=saved.text; item.version=saved.version; item.meta=saved; item.dirty=item.text!==item.saved;
                    item.row.comments[item.week+':'+item.kind]=saved;
                } catch(error) { item.error=error; item.dirty=true; }
            })();
            this.notify(item);
            await item.pending; item.pending=null; this.notify(item);
            if (!item.error && item.dirty) return this.save(item);
            return !item.error;
        }
        async flush() { return (await Promise.all([...this.items.values()].map(item=>this.save(item)))).every(Boolean); }
        dirty() { return [...this.items.values()].some(item=>item.dirty || item.pending || item.error); }
        resolve(item, latest, keepDraft) {
            item.version=latest?.version || 0; item.saved=latest?.text || ''; item.meta=latest;
            if (!keepDraft) item.text=item.saved;
            item.row.comments[item.week+':'+item.kind]=latest;
            item.dirty=item.text!==item.saved; item.error=null; this.notify(item);
        }
    }
    scope.EphemeridesModel={sum,ratio,unique,shared,summary,monday,shift,moscowToday,canEditComment,weeklyCommentColumns,commentPresentation,Drafts};
})(typeof window==='undefined'?globalThis:window);
