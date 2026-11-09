from uuid import uuid4

import pytest

from amazon_aigc.config import PRODUCTION_BASE, PRODUCTION_TABLE, Settings
from amazon_aigc.domain import editing_status, task_command
from amazon_aigc.legacy import cohort, migrate
from amazon_aigc.notifications import content
from amazon_aigc.sync import projection, verify_projection


def production(**kw):
    kw.setdefault('base_parent_origin','https://blk_000000000000000000000002.feishupkg.com')
    return Settings(database_url='postgresql://unused/test', environment='production',
                    production_enabled=True, feishu_app_id='cli_0000000000000002', personnel_table_id='tblExamplePeople', base_id=PRODUCTION_BASE, sku_table_id=PRODUCTION_TABLE,
                    feishu_mode='openapi', public_url='https://workbench.example.com',
                    base_auth_table='tblExamplea84494c0', base_view_id='vewProduction', **kw)


def test_environment_targets_fail_closed():
    assert production().base_id == PRODUCTION_BASE
    with pytest.raises(ValueError):
        Settings(database_url='postgresql://test/db', base_id=PRODUCTION_BASE, sku_table_id=PRODUCTION_TABLE)
    with pytest.raises(ValueError):
        Settings(database_url='postgresql://test/db', environment='production')
    with pytest.raises(ValueError):
        production(base_parent_origin='https://attacker.example')
    assert '【联调】' not in content({'title':'图稿已交付'}, production())['zh_cn']['title']
    assert PRODUCTION_BASE in content({'title':'图稿已交付'}, production())['zh_cn']['content'][-1][0]['href']


def rows_for(c, users):
    for key in ['operator','artist']:
        c.execute("INSERT INTO external_identities VALUES('feishu','example',%s,%s)", (str(users[key]['id']), users[key]['id']))
    rows=[]
    for state in ['待制图','待分配','制图中','待验收','待返修','已验收']:
        row={'record_id':'rec'+uuid4().hex,'选品SKU':state,'中文品名':'测试夹子','目标站点':['AU'],
             '审核状态':['审核通过'],'制图状态':[state],'运营小组':['一部一组'],
             '提交运营':[{'id':str(users['operator']['id'])}], '美工':[{'id':str(users['artist']['id'])}],
             '制图需求说明':'【产品与销售组合】四个夹子【使用说明】夹持物品【画面重点】保持四件实物',
             '参考链接':'https://amazon.com/dp/B0TEST0003','1688货源':'https://detail.1688.com/offer/123456.html'}
        if state not in ['待制图','待分配']:
            row['首次开工时间']='2026-11-01T09:00:00+08:00'
            row['制图截止日期']='2026-11-08T00:00:00+08:00'
        if state in ['待验收','待返修','已验收']:
            row['成品交付日期']='2026-11-03T09:00:00+08:00'
            row['成品图']=[{'file_token':'testtoken123456','name':'MAIN.jpg','size':123}, {'file_token':'archivetoken123456','name':'old.zip','size':234}]
        if state=='待返修':row['验收意见']='调整颜色'
        rows.append(row)
    return rows


def test_cutover_cohorts_snapshots_idempotence_and_no_notifications(db, setup):
    users,_,_=setup
    with db.connection() as c:
        rows=rows_for(c,users)
        expected={'reset':2,'active':3,'history':1,'unapproved':0}
        assert migrate(c,production(),rows)==expected
        assert not c.execute('SELECT 1 FROM legacy_migration_records WHERE record_id=%s',(rows[0]['record_id'],)).fetchone()
        before=c.execute('SELECT count(*) n FROM notification_outbox').fetchone()['n']
        assert migrate(c,production(),rows,commit=True)==expected
        assert migrate(c,production(),rows,commit=True)==expected
        assert c.execute('SELECT count(*) n FROM notification_outbox').fetchone()['n']==before
        for row in rows:
            m=c.execute('SELECT * FROM legacy_migration_records WHERE record_id=%s',(row['record_id'],)).fetchone()
            assert m['snapshot']==row
            if m['cohort']=='history':assert m['work_item_id'] is None;continue
            d=c.execute('SELECT * FROM content_documents WHERE work_item_id=%s',(m['work_item_id'],)).fetchone()
            t=c.execute('SELECT * FROM artwork_tasks WHERE document_id=%s',(d['id'],)).fetchone()
            assert d['facts_version'] is None
            if m['cohort']=='reset':assert t is None;assert editing_status(d)['stage']=='facts'
            else:
                assert t['assignee_id']==users['artist']['id'] and t['legacy_takeover']
                projected = projection(c, m['work_item_id'])
                assert projected['制图需求说明'] == row['制图需求说明']
                assert projected['计划制图数量'] == row.get('计划制图数量')
                assert t['due_date'].isoformat() == '2026-11-08'
                assert not editing_status(d,t)['pending']
                assert t['first_review_result'] is None
                if t['state']=='revision':
                    assert c.execute('SELECT review_note FROM artwork_deliveries WHERE id=%s',(t['current_delivery_id'],)).fetchone()['review_note']=='调整颜色'
                if t['state']=='pending':
                    task_command(c,users['operator'],t['id'],'review',0,{'result':'accepted'})
                    assert c.execute('SELECT first_review_result FROM artwork_tasks WHERE id=%s',(t['id'],)).fetchone()['first_review_result'] is None
                    task_command(c,users['operator'],t['id'],'cancel-review',1,{'reason':'误点'})
                    assert c.execute('SELECT first_review_result FROM artwork_tasks WHERE id=%s',(t['id'],)).fetchone()['first_review_result'] is None
        changed={**rows[0],'中文品名':'已修改'}
        with pytest.raises(ValueError,match='changed'):
            migrate(c,production(),[changed],commit=True)


def test_no_reset_started_and_projection_verifies_real_artwork():
    with pytest.raises(ValueError,match='evidence'):
        cohort({'record_id':'recBad','审核状态':['审核通过'],'制图状态':['待制图'],'首次开工时间':123})
    fields={'编辑资料':'ref','制图状态':['待验收'],'美工':[{'id':'on_1'}],'成品图':[{'file_token':'a'}],'需求提交时间':1793509200000}
    verify_projection({**fields,'制图状态':'待验收','美工':[{'id':'on_1','name':'名字'}]},fields)
    with pytest.raises(RuntimeError,match='制图状态'):
        verify_projection({**fields,'制图状态':['待制图']},fields)


def test_initial_takeover_projects_only_reference_for_active_tasks(db, setup):
    from psycopg.types.json import Jsonb

    from amazon_aigc.sync import sync_outbox
    users, _, _ = setup
    with db.connection() as c:
        rows = rows_for(c, users)
        migrate(c, production(), rows, commit=True)
        c.execute("UPDATE sync_outbox SET state='succeeded' WHERE state IN ('queued','failed')")
        migrated = c.execute("SELECT work_item_id,record_id,cohort FROM legacy_migration_records WHERE work_item_id IS NOT NULL AND record_id=ANY(%s)", ([r["record_id"] for r in rows],)).fetchall()
        for row in migrated:
            c.execute('INSERT INTO sync_outbox(work_item_id,payload) VALUES(%s,%s)',
                      (row['work_item_id'], Jsonb({'legacy_reference_only': row['cohort'] == 'active'})))

    class Gateway:
        s = production(sync_enabled=True)
        def __init__(self):
            self.written = {}

        def update(self, record_id, fields):
            self.written[record_id] = fields

        def record(self, record_id):
            return self.written[record_id]

    gateway = Gateway()
    assert sync_outbox(db, gateway) >= len(migrated)
    for row in migrated:
        fields = gateway.written[row['record_id']]
        if row['cohort'] == 'active':
            assert set(fields) == {'编辑资料', '刊登状态'}
        else:
            assert fields['制图状态'] == ['待分配'] and fields['美工'] is None
