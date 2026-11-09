from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from psycopg.types.json import Jsonb

from amazon_aigc.domain import detail, reopen_edit
from amazon_aigc.workbench_query import workbench_page, workbench_summary


def add(c, owner, *, ago=0, state=None, artist=None, complete=False, prefix='PAGE'):
    wid,did,fv,lv,bv,tid,dv,target,pub=[uuid4() for _ in range(9)]
    c.execute("INSERT INTO work_items(id,environment,site,code,name,owner_id,group_name,review_status,created_at,review_snapshot) VALUES(%s,'test','CA',%s,'收纳商品',%s,%s,'审核通过',now()-%s*interval '1 day',%s)",
              (wid,prefix+'-'+str(wid),owner['id'],owner['groups'][0],ago,Jsonb({'记录序号':'R-9000','secret_finance':'private'})))
    c.execute('INSERT INTO content_documents(id,work_item_id) VALUES(%s,%s)',(did,wid))
    if state or complete:
        for v,k in [(fv,'facts'),(lv,'listing'),(bv,'brief')]:c.execute('INSERT INTO content_versions(id,document_id,kind,body,created_by) VALUES(%s,%s,%s,%s,%s)',(v,did,k,Jsonb({'name':k}),owner['id']))
        c.execute('UPDATE content_documents SET facts_version=%s,listing_version=%s,brief_version=%s WHERE id=%s',(fv,lv,bv,did))
        c.execute("INSERT INTO artwork_tasks(id,document_id,brief_version,batch,state,assignee_id,created_at) VALUES(%s,%s,%s,1,%s,%s,now()-interval '4 days')",(tid,did,bv,state or 'accepted',artist['id'] if artist else None))
    if state in ('pending','accepted','revision') or complete:
        c.execute("INSERT INTO artwork_deliveries(id,task_id,version,asset_ids,delivered_by,delivered_at,result,image_notes) VALUES(%s,%s,1,'{}',%s,now()-interval '2 days',%s,%s)",(dv,tid,(artist or owner)['id'],'accepted' if complete else None,Jsonb({'big_notes':'x'*5000})))
        c.execute('UPDATE artwork_tasks SET current_delivery_id=%s WHERE id=%s',(dv,tid))
    if complete:
        c.execute("INSERT INTO publication_targets(id,document_id,store,msku) VALUES(%s,%s,'demo','DEMO')",(target,did))
        c.execute('UPDATE content_documents SET publication_target_id=%s WHERE id=%s',(target,did))
        c.execute("INSERT INTO publication_records(id,document_id,target_id,listing_version,store,msku,receipt,created_by) VALUES(%s,%s,%s,%s,'demo','DEMO','test',%s)",(pub,did,target,lv,owner['id']))
        c.execute("INSERT INTO image_receipts(id,publication_id,target_id,delivery_id,receipt,created_by) VALUES(%s,%s,%s,%s,'test',%s)",(uuid4(),pub,target,dv,owner['id']))
    return wid,did,tid,dv


def test_sql_pagination_counts_and_history_search(db,setup):
    users,base_item,_=setup;u=users['operator']
    with db.connection() as c:
        c.execute("UPDATE work_items SET code='BASEONLY' WHERE id=%s",(base_item,))
        for i in range(45):add(c,u,ago=40 if i>=30 else 0)
        for i in range(20):add(c,users['other'])
        p=workbench_page(c,u,scope='all')
        assert p['total']==31 and len(p['items'])==20 and p['pages']==2
        p2=workbench_page(c,u,scope='all',page=2)
        assert len(p2['items'])==11 and not ({r['id'] for r in p['items']}&{r['id'] for r in p2['items']})
        assert workbench_page(c,u,scope='all',q='page',page=3)['total']==45
        assert workbench_page(c,u,scope='pending')['total']==46
        assert workbench_page(c,u,scope='all',q='cA')['total']==45
        assert workbench_page(c,u,scope='all',q=u['name'])['total']==46
        assert workbench_page(c,u,q='%')['total']==0
        assert all(r['owner_id']==str(u['id']) for r in p['items'])
        assert p['options']['owners']==[{'id':str(u['id']),'name':u['name']}]
        summary=workbench_summary(c,u)
        assert summary['badges']['facts']==46 and summary['editor_total']==46


def test_completion_withdrawal_and_version_reopen(db,setup):
    users,_,_=setup;u=users['operator']
    with db.connection() as c:
        wid,did,tid,dv=add(c,u,complete=True,artist=users['artist'])
        assert workbench_page(c,u,scope='all',q='page')['total']==0
        assert workbench_page(c,u,tab='copyPublication')['total']==1
        assert workbench_page(c,u,tab='imagePublication')['total']==1
        c.execute('UPDATE image_receipts SET withdrawn_at=now() WHERE delivery_id=%s',(dv,))
        assert workbench_page(c,u,scope='all',q='page')['total']==1
        c.execute('UPDATE image_receipts SET withdrawn_at=NULL WHERE delivery_id=%s',(dv,))
        reopen_edit(c,u,wid,0,{'section':'listing'})
        p=workbench_page(c,u,scope='all',q='page')
        assert p['total']==1 and p['items'][0]['copy_state']=='ready'
        assert workbench_summary(c,u)['badges']['listing']==2
        assert c.execute('SELECT count(*) AS n FROM publication_records WHERE document_id=%s',(did,)).fetchone()['n']==1
        assert c.execute('SELECT count(*) AS n FROM artwork_deliveries WHERE task_id=%s',(tid,)).fetchone()['n']==1


def test_artist_permissions_review_and_projection(db,setup):
    users,_,_=setup;artist=users['artist'];owner=users['operator']
    with db.connection() as c:
        add(c,owner,state='pool')
        wid,_,_,_=add(c,owner,state='pending',artist=artist)
        add(c,users['other'],state='pending',artist=users['art_lead'])
        rows=workbench_page(c,artist,tab='tasks',task_range='pool',owner=str(owner['id']))
        assert rows['total']==1
        rows=workbench_page(c,artist,tab='mine')
        assert rows['total']==1 and rows['page_size']==50
        row=rows['items'][0]
        assert row['submitted_at'] and row['delivered_at']
        assert 'image_notes' not in row['delivery'] and 'review_snapshot' not in row
        assert row['task_number']=='R-9000'
        assert workbench_page(c,artist,tab='review')['total']==1
        assert workbench_page(c,users['art_lead'],tab='review',owner=str(owner['id']))['total']==1
        with pytest.raises(HTTPException):workbench_page(c,artist,tab='facts')
        d=detail(c,artist,wid,compact=True)
        assert d['compact'] and len(d['versions'])==3


def test_supervisor_and_admin_preview_scope(db,setup):
    users,_,_=setup;u=users['operator']
    supervisor={**u,'roles':['supervisor']};admin={**u,'roles':['admin']}
    with db.connection() as c:
        add(c,u,state='working',artist=users['artist'])
        add(c,users['other'],state='working',artist=users['art_lead'])
        assert workbench_page(c,supervisor,tab='tasks',owner=str(u['id']))['total']==1
        assert workbench_page(c,supervisor,tab='tasks',owner=str(users['other']['id']))['total']==0
        preview={**admin,'preview_artist_id':str(users['art_lead']['id']),'read_only_preview':True}
        p=workbench_page(c,preview,tab='mine')
        assert p['total']==1 and p['items'][0]['task']['assignee_id']==str(users['art_lead']['id'])
        all_preview={**admin,'preview_all_artists':True,'read_only_preview':True}
        assert workbench_page(c,all_preview,tab='mine',q='page')['total']>=2
        with pytest.raises(HTTPException):workbench_page(c,{**u,'roles_managed':True,'roles_synced_at':datetime.now(UTC)-timedelta(minutes=3)})


def test_dates_inclusive_and_no_private_option_leak(db,setup):
    users,_,_=setup;u=users['operator'];today=date.today()
    with db.connection() as c:
        add(c,u,ago=1);add(c,users['other'])
        p=workbench_page(c,u,scope='all',date_from=today,date_to=today)
        assert p['total']==1
        assert users['other']['name'] not in str(p['options'])
        with pytest.raises(HTTPException):workbench_page(c,u,scope='all',date_from=today,date_to=today-timedelta(days=1))


def test_http_paging_etag_and_authorization(db, setup, tmp_path):
    from fastapi.testclient import TestClient

    from amazon_aigc.api import create_app
    from amazon_aigc.auth import new_session
    from amazon_aigc.config import Settings

    users, item, _ = setup
    with db.connection() as c:
        for _ in range(56):
            add(c, users['operator'], state='pool')
        headers = {k: {'Authorization': 'Bearer '+new_session(c,u['id'])} for k,u in users.items()}
        c.execute('UPDATE work_items SET review_snapshot=%s WHERE id=%s', (Jsonb({'记录序号':[{'text':'R-HTTP'}]}),item))
    client = TestClient(create_app(Settings(database_url='postgresql://unused/test', storage_root=tmp_path),db))
    path='/api/workbench/items?tab=tasks&task_range=pool'
    r=client.get(path,headers=headers['operator'])
    assert r.status_code==200 and len(r.json()['items'])==50 and r.json()['total']==56
    r2=client.get(path+'&page=2',headers=headers['operator'])
    assert len(r2.json()['items'])==6
    assert not ({i['id'] for i in r.json()['items']}&{i['id'] for i in r2.json()['items']})
    cached={**headers['operator'],'If-None-Match':r.headers['etag']}
    assert client.get(path,headers=cached).status_code==304
    assert client.get(path,headers={'If-None-Match':r.headers['etag']}).status_code==401
    assert client.get(path,headers={**headers['other'],'If-None-Match':r.headers['etag']}).json()['total']==0
    assert client.get('/api/workbench/items?tab=facts',headers=headers['artist']).status_code==403
    assert client.get('/api/workbench/items?tab=facts&q=r-http',headers=headers['operator']).json()['items'][0]['task_number']=='R-HTTP'
    assert client.get('/api/workbench/items?tab=facts&page=999',headers=headers['operator']).json()['page']==1
    with db.connection() as c:
        c.execute('UPDATE users SET active=false WHERE id=%s',(users['operator']['id'],))
    assert client.get(path,headers=cached).status_code==401


def test_completion_does_not_hide_changed_brief_or_cancelled_review(db,setup):
    users,_,_=setup
    with db.connection() as c:
        wid,did,_tid,dv=add(c,users['operator'],complete=True,artist=users['artist'])
        query=lambda: workbench_page(c,users['operator'],scope='all',q='page')['total']
        assert query()==0
        c.execute('UPDATE image_receipts SET invalidated_at=now() WHERE delivery_id=%s',(dv,))
        assert query()==1
        c.execute('UPDATE image_receipts SET invalidated_at=NULL WHERE delivery_id=%s',(dv,))
        c.execute('UPDATE publication_records SET withdrawn_at=now() WHERE document_id=%s',(did,))
        assert query()==1
        c.execute('UPDATE publication_records SET withdrawn_at=NULL WHERE document_id=%s',(did,))
        assert query()==0
        reopen_edit(c,users['operator'],wid,0,{'section':'brief'})
        assert query()==1
        assert workbench_page(c,users['operator'],tab='imagePublication')['total']==1


def test_review_time_tracks_current_delivery_and_cancellation(db,setup):
    from amazon_aigc.domain import task_command
    users,_,_=setup;u=users['operator']
    with db.connection() as c:
        wid,_did,tid,dv=add(c,u,state='pending',artist=users['artist'])
        def row():
            return next(r for r in workbench_page(c,u,tab='review',state='')['items'] if r['id']==str(wid))
        assert row()['reviewed_at'] is None
        task_command(c,u,tid,'review',0,{'delivery_id':str(dv),'result':'accepted','image_notes':{}})
        assert row()['reviewed_at']
        task_command(c,u,tid,'cancel-review',1,{'delivery_id':str(dv)})
        assert row()['reviewed_at'] is None
        task_command(c,u,tid,'review',2,{'delivery_id':str(dv),'result':'rejected','note':'test repair','image_notes':{}})
        assert row()['reviewed_at'] and row()['delivery']['result']=='rejected'
