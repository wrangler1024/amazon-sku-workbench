from uuid import uuid4

from psycopg.types.json import Jsonb
from test_unified_artwork import extra_task
from test_workflow import pool

from amazon_aigc.legacy_first_pass import replay
from amazon_aigc.workbench_views import artist_rows


def test_history_requires_system_delivery_verdict_and_preserves_first_rejection():
    def event(rev,before,after,operator='多维表格助手'):
        return {'rev':rev,'create_time':rev,'operator':operator,'field_changes':[{'field_name':'制图进度留存','before':before,'after':after}]}
    history={'complete':True,'events':[{'rev':1,'create_time':1,'operator':'主管','field_changes':[{'field_name':'美工','before':'','after':'画师'}]},event(2,'制图中','待验收'),event(3,'待验收','待返修'),event(4,'待返修','待验收'),event(5,'待验收','已验收')]}
    verdicts,issues=replay(history,{'制图状态':['已验收'],'制图进度留存':['已验收']},10)
    assert issues==[] and verdicts['画师']['result']=='rejected'
    verdicts,issues=replay(history,{'制图状态':['待验收'],'制图进度留存':['待验收']},2)
    assert issues==[] and not verdicts
    history['events'][2]['operator']='手工修改'
    assert 'manual_review_phase' in replay(history,{'制图状态':['已验收'],'制图进度留存':['已验收']},10)[1]


def test_baseline_and_live_rates_deduplicate_takeover_and_recompute_cancellation(db, setup):
    users,item,_=setup
    artist=users['artist']['id'];tid=pool(db,setup)['id'];other=extra_task(db,setup,2)['id'];rid='rec'+uuid4().hex
    with db.connection() as c:
        c.execute("INSERT INTO legacy_migration_records(base_id,table_id,record_id,cohort,fingerprint,snapshot,work_item_id,artist_id) VALUES('base','table',%s,'active','hash',%s,%s,%s)",(rid,Jsonb({}),item,artist))
        c.execute("INSERT INTO legacy_first_reviews(base_id,table_id,record_id,artist_id,result,review_rev,review_time,source_sha256) VALUES('base','table',%s,%s,'rejected',3,now(),'hash')",(rid,artist))
        c.execute("UPDATE artwork_tasks SET state='accepted',assignee_id=%s,legacy_takeover=true,first_review_result='accepted' WHERE id=%s",(artist,tid))
        assert next(r for r in artist_rows(c) if r['id']==str(artist))['first_pass_rate']==0
        c.execute("UPDATE artwork_tasks SET state='accepted',assignee_id=%s,first_review_result='accepted' WHERE id=%s",(artist,other))
        assert next(r for r in artist_rows(c) if r['id']==str(artist))['first_pass_rate']==0.5
        c.execute("UPDATE artwork_tasks SET first_review_result=NULL WHERE id=%s",(other,))
        assert next(r for r in artist_rows(c) if r['id']==str(artist))['first_pass_rate']==0
