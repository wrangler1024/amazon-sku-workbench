"""Permission-scoped, version-aware workbench list projections.

No historic business rows are deleted. Completion only changes queue membership.
The legacy /api/items response remains available to older published plugins.
"""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from .domain import fail, serial
from .roster_sync import fresh

EDITOR_TABS = ('facts', 'listing', 'brief')
TABS = (*EDITOR_TABS, 'tasks', 'mine', 'review', 'copyPublication', 'imagePublication')
SITE_NAMES = {'US':'美国', 'CA':'加拿大', 'AU':'澳大利亚', 'UK':'英国', 'DE':'德国', 'FR':'法国', 'IT':'意大利', 'ES':'西班牙', 'JP':'日本', 'MX':'墨西哥', 'AE':'阿联酋', 'SA':'沙特', 'NL':'荷兰', 'SE':'瑞典', 'PL':'波兰', 'BE':'比利时', 'IE':'爱尔兰', 'SG':'新加坡'}


def permission(user):
    """Equivalent to can_read, expressed once in SQL before LIMIT and aggregates."""
    if not fresh(user):
        fail('人员权限需要重新核验，请刷新工作台', 403)
    roles = user['roles']
    args = {'actor': user['id'], 'groups': user.get('groups') or [], 'preview': user.get('preview_artist_id')}
    if 'admin' in roles:
        clause = 'true'
    else:
        parts = ['w.owner_id=%(actor)s']
        if 'supervisor' in roles:
            parts.append('w.group_name=ANY(%(groups)s)')
        if 'art_lead' in roles:
            parts.append("EXISTS(SELECT 1 FROM artwork_tasks x WHERE x.document_id=d.id AND x.state<>'withdrawn')")
        elif 'artist' in roles:
            parts.append("EXISTS(SELECT 1 FROM artwork_tasks x WHERE x.document_id=d.id AND (x.assignee_id=%(actor)s OR x.state='pool'))")
        clause = '('+' OR '.join(parts)+')'
    if user.get('preview_artist_id') or user.get('preview_all_artists'):
        if 'admin' not in roles:
            fail('只读预览仅管理员可用', 403)
        clause += " AND t.state NOT IN ('pool','withdrawn')"
        if user.get('preview_all_artists'):
            clause += """ AND EXISTS(SELECT 1 FROM users a WHERE a.id=t.assignee_id AND a.active AND a.internal
              AND ('artist'=ANY(a.roles) OR 'art_lead'=ANY(a.roles)) AND a.verified_at>now()-interval '1 day'
              AND (NOT a.roles_managed OR (a.roles_synced_at>now()-interval '120 seconds' AND a.verified_at>now()-interval '600 seconds')))"""
        else:
            clause += ' AND t.assignee_id=%(preview)s'
    return clause, args


def model(user, tab=""):
    allowed, args = permission(user)
    market_art = "true" if tab in ("review", "imagePublication") else "false"
    market_copy = "true" if tab == "copyPublication" else "false"
    return f"""WITH base AS (
      SELECT w.id,w.environment,coalesce(am.site,mc.site,w.site) AS site,w.site AS review_site,coalesce(am.site,mc.site) AS marketplace,w.code,w.name,w.owner_id,w.group_name,w.review_status,w.created_at,w.updated_at,
        w.editor_mode,w.editor_targets,mp.shared_version AS market_brief_version,t.market_sites,
        coalesce((SELECT jsonb_agg(jsonb_build_object('site',m.site,'state',m.state,'current_delivery_id',m.current_delivery_id)) FROM artwork_task_markets m WHERE m.task_id=t.id),'[]'::jsonb) AS task_markets,
        (cardinality(w.editor_targets)=0 OR EXISTS(SELECT 1 FROM unnest(w.editor_targets) s LEFT JOIN market_contents m ON m.document_id=d.id AND m.site=s LEFT JOIN content_versions v ON v.id=m.listing_version WHERE v.id IS NULL OR v.facts_version IS DISTINCT FROM d.facts_version OR v.body IS DISTINCT FROM m.listing)) AS market_pending_listing,
        (NOT EXISTS(SELECT 1 FROM market_submissions ms LEFT JOIN artwork_tasks mt ON mt.id=ms.task_id WHERE ms.brief_version=mp.shared_version AND (ms.state='queued' OR mt.state<>'withdrawn')) OR cardinality(w.editor_targets)=0 OR mv.id IS NULL OR mv.facts_version IS DISTINCT FROM d.facts_version OR (mv.body-'market_texts'-'market_sites'-'shared_design') IS DISTINCT FROM mp.shared_brief OR EXISTS(SELECT 1 FROM unnest(w.editor_targets) s LEFT JOIN market_contents m ON m.document_id=d.id AND m.site=s WHERE NOT (mv.body->'market_sites' ? s) OR m.image_texts IS DISTINCT FROM mv.body->'market_texts'->s)) AS market_pending_brief,
        EXISTS(SELECT 1 FROM market_contents m WHERE m.document_id=d.id AND m.site=ANY(w.editor_targets) AND m.listing_version IS NOT NULL) AS market_has_copy,
        EXISTS(SELECT 1 FROM market_contents m WHERE m.document_id=d.id AND m.site=ANY(w.editor_targets) AND m.listing_version IS NOT NULL AND NOT EXISTS(SELECT 1 FROM publication_records p WHERE p.target_id=m.publication_target_id AND p.listing_version=m.listing_version AND p.withdrawn_at IS NULL)) AS market_copy_ready,
        EXISTS(SELECT 1 FROM artwork_task_markets m WHERE m.task_id=t.id AND m.state='accepted') AS market_has_images,
        EXISTS(SELECT 1 FROM artwork_task_markets m WHERE m.task_id=t.id AND m.state='accepted' AND NOT EXISTS(SELECT 1 FROM image_receipts r WHERE r.delivery_id=m.current_delivery_id AND r.invalidated_at IS NULL AND r.withdrawn_at IS NULL)) AS market_images_ready,
        u.name AS owner_name,CASE jsonb_typeof(w.review_snapshot->'记录序号')
          WHEN 'array' THEN (SELECT string_agg(coalesce(v->>'text',''),'') FROM jsonb_array_elements(w.review_snapshot->'记录序号') v)
          ELSE coalesce(w.review_snapshot->>'记录序号','') END AS task_number,
        coalesce(w.review_snapshot->'产品图片'->0->>'file_token','') AS thumbnail_token,
        d.id AS document_id,d.revision,d.facts_version,CASE WHEN mc.site IS NOT NULL THEN mc.listing_version ELSE d.listing_version END AS listing_version,d.brief_version,
        coalesce(d.body->'legacy_edit_request','{{}}'::jsonb) AS legacy_edit_request,
        t.id AS task_id,coalesce(am.state,t.state) AS task_state,t.assignee_id,t.urgent,t.due_date,t.batch,
        t.revision AS task_revision,t.brief_version AS task_brief_version,t.legacy_takeover,
        CASE WHEN am.site IS NOT NULL THEN am.current_delivery_id ELSE t.current_delivery_id END AS current_delivery_id,t.created_at AS submitted_at,
        ad.id AS delivery_id,ad.result AS delivery_result,ad.delivered_at,ad.version AS delivery_version,
        CASE WHEN ad.result IN ('accepted','rejected') THEN ad.reviewed_at END AS reviewed_at,
        p.id AS publication_id,p.listing_version AS published_version,p.created_at AS copy_published_at,
        ir.created_at AS image_published_at,pt.id AS target_id,pt.store,pt.msku,
        EXISTS(SELECT 1 FROM publication_records x WHERE x.document_id=d.id AND (mc.site IS NULL OR x.target_id=mc.publication_target_id)) AS copy_history,
        EXISTS(SELECT 1 FROM image_receipts x JOIN artwork_deliveries xd ON xd.id=x.delivery_id JOIN artwork_tasks xt ON xt.id=xd.task_id WHERE xt.document_id=d.id AND (am.site IS NULL OR xd.marketplace=am.site)) AS image_history,
        EXISTS(SELECT 1 FROM image_receipts x JOIN artwork_deliveries xd ON xd.id=x.delivery_id JOIN artwork_tasks xt ON xt.id=xd.task_id WHERE xt.document_id=d.id AND (am.site IS NULL OR xd.marketplace=am.site) AND x.withdrawn_at IS NULL) AS image_previous,
        coalesce(ir.delivery_id=ad.id AND ad.result='accepted' AND coalesce(am.state,t.state)='accepted',false) AS images_uploaded
      FROM work_items w JOIN content_documents d ON d.work_item_id=w.id
      LEFT JOIN users u ON u.id=w.owner_id
      LEFT JOIN market_plans mp ON mp.document_id=d.id
      LEFT JOIN content_versions mv ON mv.id=mp.shared_version
      LEFT JOIN LATERAL(SELECT * FROM artwork_tasks x WHERE x.document_id=d.id ORDER BY batch DESC LIMIT 1)t ON true
      LEFT JOIN LATERAL(SELECT m.* FROM artwork_task_markets m WHERE m.task_id=t.id AND {market_art})am ON true
      LEFT JOIN LATERAL(SELECT m.* FROM market_contents m WHERE m.document_id=d.id AND m.site=ANY(w.editor_targets) AND w.editor_mode='pan_eu' AND {market_copy})mc ON true
      LEFT JOIN artwork_deliveries ad ON ad.id=CASE WHEN am.site IS NOT NULL THEN am.current_delivery_id ELSE t.current_delivery_id END
      LEFT JOIN publication_targets pt ON pt.id=CASE WHEN mc.site IS NOT NULL THEN mc.publication_target_id ELSE d.publication_target_id END
      LEFT JOIN LATERAL(SELECT id,listing_version,created_at FROM publication_records x WHERE x.document_id=d.id AND x.target_id=pt.id AND x.withdrawn_at IS NULL ORDER BY created_at DESC,id DESC LIMIT 1)p ON true
      LEFT JOIN LATERAL(SELECT x.delivery_id,x.created_at FROM image_receipts x JOIN artwork_deliveries xd ON xd.id=x.delivery_id JOIN artwork_tasks xt ON xt.id=xd.task_id WHERE xt.document_id=d.id AND (am.site IS NULL OR xd.marketplace=am.site) AND x.invalidated_at IS NULL AND x.withdrawn_at IS NULL ORDER BY x.created_at DESC,x.id DESC LIMIT 1)ir ON true
      WHERE {allowed}
    ), flags AS (
      SELECT *,CASE WHEN editor_mode='pan_eu' THEN cardinality(editor_targets)>0 AND NOT market_pending_listing AND NOT market_pending_brief AND NOT market_copy_ready AND task_state='accepted' AND task_brief_version=market_brief_version AND market_sites @> editor_targets AND NOT market_images_ready ELSE coalesce(listing_version IS NOT NULL AND brief_version IS NOT NULL
        AND listing_version=published_version AND task_brief_version=brief_version AND images_uploaded,false) END AS completed,
        coalesce(legacy_takeover AND task_state IN ('working','pending','revision'),false) AS legacy_inflight,
        CASE WHEN editor_mode='pan_eu' AND marketplace IS NULL THEN CASE WHEN market_copy_ready THEN 'ready' ELSE 'published' END WHEN publication_id IS NULL THEN 'ready' WHEN listing_version IS NULL OR listing_version<>published_version THEN 'ready' ELSE 'published' END AS copy_state,
        CASE WHEN editor_mode='pan_eu' AND marketplace IS NULL THEN CASE WHEN market_images_ready THEN 'ready' WHEN market_has_images THEN 'uploaded' ELSE 'blocked' END WHEN task_state IS DISTINCT FROM 'accepted' OR delivery_result IS DISTINCT FROM 'accepted' THEN 'blocked'
          WHEN images_uploaded THEN 'uploaded' ELSE 'ready' END AS image_state
      FROM base
    ), model AS (
      SELECT *,NOT completed AND NOT legacy_inflight AND NOT coalesce(legacy_takeover AND task_state='accepted' AND
        (legacy_edit_request->>'task_id' IS DISTINCT FROM task_id::text OR NOT coalesce(legacy_edit_request->'sections' ? 'facts',false)),false) AND facts_version IS NULL AS pending_facts,
        NOT completed AND NOT legacy_inflight AND NOT coalesce(legacy_takeover AND task_state='accepted' AND
        (legacy_edit_request->>'task_id' IS DISTINCT FROM task_id::text OR NOT coalesce(legacy_edit_request->'sections' ? 'listing',false)),false) AND CASE WHEN editor_mode='pan_eu' THEN market_pending_listing ELSE listing_version IS NULL END AS pending_listing,
        NOT completed AND NOT legacy_inflight AND NOT coalesce(legacy_takeover AND task_state='accepted' AND
        (legacy_edit_request->>'task_id' IS DISTINCT FROM task_id::text OR NOT coalesce(legacy_edit_request->'sections' ? 'brief',false)),false) AND
        CASE WHEN editor_mode='pan_eu' THEN market_pending_brief ELSE (brief_version IS NULL OR task_id IS NULL OR task_state='withdrawn' OR task_brief_version IS DISTINCT FROM brief_version) END AS pending_brief
      FROM flags
    ) """, args


def tab_clause(tab, user):
    roles = user['roles']
    ops = any(r in roles for r in ('admin','operator','supervisor'))
    art = any(r in roles for r in ('artist','art_lead'))
    if tab not in TABS or (tab in (*EDITOR_TABS,'copyPublication','imagePublication') and not ops) or (tab=='mine' and not art and 'admin' not in roles):
        fail('无权访问此工作区', 403)
    if tab in EDITOR_TABS:
        return 'NOT completed'
    if tab == 'tasks':
        return "task_id IS NOT NULL AND task_state<>'withdrawn'"
    if tab == 'mine':
        own = 'true' if user.get('preview_all_artists') or user.get('preview_artist_id') else 'assignee_id=%(actor)s'
        return f"task_id IS NOT NULL AND assignee_id IS NOT NULL AND task_state NOT IN ('pool','withdrawn') AND {own}"
    if tab == 'review':
        return "(delivery_id IS NOT NULL OR jsonb_array_length(task_markets)>0) AND task_state IN ('pending','revision','accepted')" + ('' if ops or 'art_lead' in roles else ' AND assignee_id=%(actor)s')
    if tab == 'copyPublication':
        return '(listing_version IS NOT NULL OR (marketplace IS NULL AND market_has_copy) OR copy_history)'
    return "((task_state='accepted' AND delivery_result='accepted') OR (marketplace IS NULL AND market_has_images) OR image_history)"


def row_json(r):
    """Only fields actually used in a list, never full delivery notes or snapshots."""
    task = None if not r['task_id'] else {k: r[v] for k,v in {
        'id':'task_id','state':'task_state','assignee_id':'assignee_id','urgent':'urgent','due_date':'due_date','batch':'batch',
        'revision':'task_revision','brief_version':'task_brief_version','legacy_takeover':'legacy_takeover',
        'current_delivery_id':'current_delivery_id','created_at':'submitted_at','market_sites':'market_sites'}.items()}
    return {**{k:r[k] for k in ('id','environment','site','code','name','owner_id','owner_name','group_name','review_status','created_at','updated_at','task_number','document_id','revision','facts_version','listing_version','brief_version','legacy_edit_request','published_version','images_uploaded','completed','submitted_at','delivered_at','reviewed_at','copy_published_at','image_published_at')},
        'review_site':r['review_site'],'marketplace':r['marketplace'],'editor_mode':r['editor_mode'],'editor_targets':r['editor_targets'],'task_markets':r['task_markets'],'market_has_copy':r['market_has_copy'],'market_has_images':r['market_has_images'],
        'market_editing':{'facts':r['pending_facts'],'listing':r['pending_listing'],'brief':r['pending_brief']} if r['editor_mode']=='pan_eu' else None,
        'thumbnail_id':r['thumbnail_id'],'thumbnail_pending':bool(r['thumbnail_token'] and not r['thumbnail_id']),'task':task,
        'delivery': None if not r['delivery_id'] else {'id':r['delivery_id'],'result':r['delivery_result'],'version':r['delivery_version'],'delivered_at':r['delivered_at']},
        'publication':None if not r['publication_id'] else {'id':r['publication_id'],'listing_version':r['published_version']},
        'publication_target':None if not r['target_id'] else {'id':r['target_id'],'store':r['store'],'msku':r['msku']},
        'publication_count':int(r['publication_id'] is not None),'image_history_count':int(r['image_history']),
        'image_previous_count':int(r['image_previous']),'copy_state':r['copy_state'],'image_state':r['image_state']}


def workbench_page(c, user, *, tab='facts', scope='pending', q='', days=7, date_from=None, date_to=None,
                   page=1, state='', task_range='assigned', site='', owner='', group='', person='', urgent=False):
    cte,args=model(user,tab)
    if tab in ('copyPublication','imagePublication') and state=='update': state='ready'
    if tab=='imagePublication' and state=='unlisted': state='ready'
    base=tab_clause(tab,user)
    page_size=20 if tab in EDITOR_TABS else 50
    common=[base]
    q=q.strip()
    if q:
        args['query']='%'+q.lower().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        names='CASE site '+' '.join("WHEN '"+k+"' THEN '"+v+"'" for k,v in SITE_NAMES.items())+" ELSE '' END"
        common.append("lower(concat_ws(' ',name,code,task_number,owner_name,site,"+names+")) LIKE %(query)s ESCAPE '\\'")
    for key,value,column in [('site',site,'site'),('owner',owner,'owner_id::text'),('group_filter',group,'group_name'),('person',person,'assignee_id::text')]:
        if value:
            args[key]=value
            common.append(f'{column}=%({key})s')
    if urgent:
        common.append('urgent IS TRUE')
    common_where=' AND '.join(common)
    scoped=list(common)
    if tab=='tasks':
        scoped.append("task_state='pool'" if task_range=='pool' else "task_state<>'pool'")
    if tab in EDITOR_TABS and not q:
        if scope=='pending': scoped.append('pending_'+tab)
        elif scope=='confirmed': scoped.append('facts_version IS NOT NULL' if tab=='facts' else 'NOT pending_'+tab)
        elif scope!='all': fail('无效任务范围',422)
        if scope=='all':
            tz=ZoneInfo('Asia/Shanghai')
            if date_from or date_to:
                if not date_from or not date_to or date_from>date_to: fail('请选择有效起止日期',422)
                start,end=date_from,date_to
            elif days:
                end=datetime.now(tz).date();start=end-timedelta(days=days-1)
            else: start=end=None
            if start:
                args['start']=datetime.combine(start,time.min,tz);args['end']=datetime.combine(end+timedelta(days=1),time.min,tz)
                scoped.extend(['created_at>=%(start)s','created_at<%(end)s'])
    scoped_where=' AND '.join(scoped)
    status_column='copy_state' if tab=='copyPublication' else 'image_state' if tab=='imagePublication' else 'task_state'
    states=[] if tab in EDITOR_TABS else ['ready','published'] if tab=='copyPublication' else ['blocked','ready','uploaded'] if tab=='imagePublication' else ['pool','waiting','working','revision','pending','accepted']
    count_sql=','.join(f"count(*) FILTER(WHERE {status_column}='{key}') AS \"{key}\"" for key in states)
    counts=c.execute(cte+f"SELECT count(*) AS total{','+count_sql if count_sql else ''} FROM model WHERE {scoped_where}",args).fetchone()
    if tab=='mine': counts['unfinished']=counts['total']-counts['accepted']
    final=list(scoped)
    if tab not in EDITOR_TABS and not (tab=='tasks' and task_range=='pool'):
        if state=='unfinished': final.append("task_state<>'accepted'")
        elif state=='todo': final.append("image_state='ready'" if tab=='imagePublication' else "copy_state<>'published'")
        elif state:
            if state not in states: fail('无效状态筛选',422)
            args['state']=state;final.append(f'{status_column}=%(state)s')
    where=' AND '.join(final)
    total=c.execute(cte+f'SELECT count(*) AS total FROM model WHERE {where}',args).fetchone()['total']
    page=min(max(1,page),max(1,(total+page_size-1)//page_size));args.update(limit=page_size,offset=(page-1)*page_size)
    order='created_at DESC,id DESC' if tab in (*EDITOR_TABS,'copyPublication','imagePublication') else 'urgent DESC NULLS LAST,due_date ASC NULLS LAST,submitted_at DESC,id DESC'
    rows=c.execute(cte+f""", picked AS (SELECT * FROM model WHERE {where} ORDER BY {order} LIMIT %(limit)s OFFSET %(offset)s)
      SELECT p.*,a.id AS thumbnail_id FROM picked p LEFT JOIN LATERAL(
        SELECT id FROM assets WHERE work_item_id=p.id AND (CASE WHEN p.thumbnail_token<>'' THEN source_url='feishu-product-image:'||p.thumbnail_token
          ELSE source_url IS NULL OR source_url NOT LIKE 'feishu-product-image:%%' END) ORDER BY created_at,id LIMIT 1)a ON true ORDER BY {order}""",args).fetchall()
    # Options use the entire authorized tab, not the current page. Group restricts operators only.
    option_rows=c.execute(cte+f"SELECT DISTINCT site,group_name,owner_id,owner_name,assignee_id FROM model WHERE {base}",args).fetchall()
    owners={str(r['owner_id']):r['owner_name'] for r in option_rows if r['owner_id'] and (not group or r['group_name']==group)}
    options={'sites':sorted({r['site'] for r in option_rows}), 'groups':sorted({r['group_name'] for r in option_rows if r['group_name']}),
             'owners':[{'id':k,'name':v} for k,v in sorted(owners.items(),key=lambda p:p[1] or '')],
             'artists':sorted({str(r['assignee_id']) for r in option_rows if r['assignee_id']})}
    ranges=c.execute(cte+f"SELECT count(*) FILTER(WHERE task_state='pool') AS pool,count(*) FILTER(WHERE task_state<>'pool') AS assigned FROM model WHERE {common_where}",args).fetchone() if tab=='tasks' else {}
    return serial({'items':[row_json(r) for r in rows],'total':total,'page':page,'page_size':page_size,'pages':max(1,(total+page_size-1)//page_size),'counts':counts,'ranges':ranges,'options':options})


def workbench_summary(c,user):
    cte,args=model(user)
    expr=[]
    for tab in TABS:
        try: scope=tab_clause(tab,user)
        except HTTPException: continue
        if tab in EDITOR_TABS:
            todo='pending_'+tab
        elif tab=='tasks':todo="task_state='pool'" if any(r in user['roles'] for r in ('artist','art_lead')) else "task_state<>'accepted'"
        elif tab=='mine':
            if 'admin' in user['roles'] and not user.get('preview_artist_id'):
                scope="task_id IS NOT NULL AND task_state NOT IN ('pool','withdrawn') AND EXISTS(SELECT 1 FROM users a WHERE a.id=model.assignee_id AND a.active AND a.internal AND ('artist'=ANY(a.roles) OR 'art_lead'=ANY(a.roles)) AND a.verified_at>now()-interval '1 day' AND (NOT a.roles_managed OR (a.roles_synced_at>now()-interval '120 seconds' AND a.verified_at>now()-interval '600 seconds')))"
            todo="task_state<>'accepted'"
        elif tab=='review':todo="task_state='pending'"
        elif tab=='copyPublication':todo="copy_state<>'published'"
        else:todo="image_state='ready'"
        expr.append(f'count(*) FILTER(WHERE ({scope}) AND ({todo})) AS "{tab}"')
    expr.extend(['count(*) FILTER(WHERE NOT completed) AS editor_total','count(*) FILTER(WHERE NOT completed AND facts_version IS NOT NULL) AS facts_confirmed'])
    result=c.execute(cte+'SELECT '+','.join(expr)+' FROM model',args).fetchone()
    for tab, todo in [('review', "task_state='pending'"), ('copyPublication', "copy_state='ready'"), ('imagePublication', "image_state='ready'")]:
        try:
            scope = tab_clause(tab, user)
        except HTTPException:
            continue
        market_cte, market_args = model(user, tab)
        result[tab] = c.execute(market_cte + f"SELECT count(*) n FROM model WHERE ({scope}) AND ({todo})", market_args).fetchone()['n']
    return {'badges':{k:result.get(k,0) for k in TABS},'editor_total':result['editor_total'],'facts_confirmed':result['facts_confirmed']}
