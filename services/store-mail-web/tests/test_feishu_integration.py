"""SSO admission/authorization and durable private notifications; no external writes."""
import json
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from dataclasses import replace
from unittest.mock import Mock

from store_mail_web.config import Settings
from store_mail_web.crypto import generate_master_key, hash_password
from store_mail_web.service import Service, ApiError
from store_mail_web.feishu import FeishuClient, FeishuError
from store_mail_web.http_api import start_http_server
from store_mail_web.models import FetchedMail, ClassifiedMail
from store_mail_web.auth_service import safe_return
from store_mail_web.direct_notify import mail_text


class IntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.settings=Settings(secret_key=generate_master_key(),admin_user='root-admin',admin_password='local-test-pass',
            db_path=Path(self.tmp.name)/'db.sqlite3',feishu_app_id='cli_test',feishu_app_secret='secret-test-only',
            feishu_tenant_key='tenant-test',public_url='https://mail.test.example')
        self.service=Service.build(self.settings)
        self.wire_fakes()
        self.store=self.service.store
        self.admin=self.store.get_user_by_username('root-admin')
        self.a=self.service.create_account({'display_name':'A','email':'a@example.invalid','auth_code':'fake'},self.admin)
        self.b=self.service.create_account({'display_name':'B','email':'b@example.invalid','auth_code':'fake'},self.admin)

    def wire_fakes(self):
        self.service.feishu.member_active=Mock(return_value=True)
        self.service.feishu.exchange=Mock(side_effect=lambda code,verifier:{'name':'员工 '+code,'open_id':code,'tenant_key':'tenant-test'})
        self.service.feishu.send_text=Mock(return_value='om_sent')

    def tearDown(self):
        if hasattr(self,'server'):
            self.server.shutdown();self.server.server_close()
        self.service.store.close();self.tmp.cleanup()

    def identity(self,name='ou_A'):
        return self.store.touch_identity('cli_test',{'name':name,'open_id':name,'tenant_key':'tenant-test'})

    def member(self,name='ou_A',aids=None):
        identity=self.identity(name)
        token=self.store.applicant_token(identity['id'])
        self.service.submit_admission(token,{'note':'负责店铺'})
        self.service.review_admission(identity['id'],{'decision':'approve','account_ids':aids or [self.a['id']]},self.admin)
        return self.store.get_user(self.store.identity(identity['id'])['user_id'])

    def insert(self,uid,aid=None,ts=None):
        mail=FetchedMail(uid,str(uid),'普通新邮件','Seller','seller@example.com','a@example.invalid',None,
            ts if ts is not None else int(time.time())+1,100,True,'body secret',None,'preview secret')
        return self.store.upsert_message(aid or self.a['id'],mail,ClassifiedMail(False,'其他','低'))

    def test_oauth_browser_binding_single_use_and_safe_redirect(self):
        started=self.service.start_feishu_auth('https://evil.example')
        query=urllib.parse.parse_qs(urllib.parse.urlsplit(started['url']).query)
        self.assertEqual(query['code_challenge_method'],['S256'])
        with self.assertRaises(ApiError):
            self.service.finish_feishu_auth('ou_A',query['state'][0],'wrong-browser')
        self.service.feishu.exchange.assert_not_called()
        result=self.service.finish_feishu_auth('ou_A',query['state'][0],started['browser'])
        self.assertIn('applicant_token',result)
        with self.assertRaises(ApiError):
            self.service.finish_feishu_auth('ou_A',query['state'][0],started['browser'])
        self.assertEqual(safe_return('//evil.example'),'/#/inbox')
        self.assertEqual(safe_return('/#/message/5'),'/#/message/5')
        self.assertNotIn('code_verifier',started['url'])

    def test_pending_review_reject_reapply_and_no_self_approval(self):
        identity=self.identity();token=self.store.applicant_token(identity['id'])
        self.service.submit_admission(token,{'note':'一次'})
        self.service.submit_admission(token,{'note':'再次扫码'})
        self.assertEqual(len(self.store.list_admissions()),1)
        self.assertEqual(self.store.identity(identity['id'])['request_note'],'一次')
        with self.assertRaises(ApiError):self.service.complete_admission(token)
        ordinary=self.store.create_user('local',hash_password('pass-123'),full_name='普通用户')
        with self.assertRaises(ApiError):self.service.review_admission(identity['id'],{'decision':'approve'},ordinary)
        self.service.review_admission(identity['id'],{'decision':'reject','note':'请联系主管'},self.admin)
        with self.assertRaises(ApiError):self.service.complete_admission(token)
        self.service.submit_admission(token,{'note':'重新申请'})
        self.service.review_admission(identity['id'],{'decision':'approve','account_ids':[self.a['id']]},self.admin)
        with self.assertRaises(ApiError):self.service.review_admission(identity['id'],{'decision':'approve'},self.admin)
        result=self.service.complete_admission(token)
        viewer=self.service.resolve_viewer(self.service.verify_session(result['token']))
        self.assertEqual(viewer.account_ids,(self.a['id'],))
        self.assertEqual(viewer.role,'supervisor')
        self.assertEqual(len(self.service.accounts_view(viewer)),1)
        with self.assertRaises(ApiError):self.service._require_visible_account(viewer,self.b['id'])

    def test_bind_existing_keeps_history_and_requires_original_session(self):
        login=self.service.login('root-admin','local-test-pass')
        start=self.service.start_feishu_auth('/#/settings',login['token'])
        state=urllib.parse.parse_qs(urllib.parse.urlsplit(start['url']).query)['state'][0]
        with self.assertRaises(ApiError):self.service.finish_feishu_auth('ou_root_admin',state,start['browser'],'wrong')
        start=self.service.start_feishu_auth('/#/settings',login['token'])
        state=urllib.parse.parse_qs(urllib.parse.urlsplit(start['url']).query)['state'][0]
        result=self.service.finish_feishu_auth('ou_root_admin',state,start['browser'],login['token'])
        self.assertEqual(result['user']['id'],self.admin.id)
        self.assertEqual(result['user']['role'],'admin')
        self.assertEqual(result['return_to'],'/#/settings')
        with self.assertRaises(ValueError):self.store.bind_identity(self.identity('ou_other')['id'],self.admin.id,self.admin.id)

    def test_review_can_link_existing_user_without_recreating(self):
        user=self.store.create_user('old',hash_password('pass-123'),account_ids=(self.a['id'],),full_name='老员工')
        self.insert(1)
        mid=self.store._conn.execute('SELECT id FROM messages').fetchone()[0]
        self.store._conn.execute('INSERT INTO message_reads VALUES (?,?,?)',(user.id,mid,'2026-11-04'));self.store._conn.commit()
        i=self.identity();self.store.request_admission(i['id'],'')
        self.service.review_admission(i['id'],{'decision':'approve','existing_user_id':user.id,'account_ids':[self.a['id']]},self.admin)
        self.assertEqual(self.store.identity(i['id'])['user_id'],user.id)
        self.assertEqual(self.store._conn.execute('SELECT count(*) FROM message_reads WHERE user_id=?',(user.id,)).fetchone()[0],1)
        with self.assertRaises(ApiError):self.service.login('old','pass-123')

    def test_logout_disable_and_directory_revocation_invalidate_sessions(self):
        user=self.member();identity=self.store.identity_for_user(user.id)
        result=self.service._issue_session(user,identity['id'])
        self.service.verify_session(result['token'])
        self.store.logout_session(result['token'])
        with self.assertRaises(ApiError):self.service.verify_session(result['token'])
        result=self.service._issue_session(user,identity['id'])
        self.store.update_user(user.id,enabled=False)
        with self.assertRaises(ApiError):self.service.verify_session(result['token'])
        self.store.update_user(user.id,enabled=True)
        with self.assertRaises(ApiError):self.service.verify_session(result['token'])
        result=self.service._issue_session(self.store.get_user(user.id),identity['id'])
        self.service.feishu.member_active.return_value=False
        with self.assertRaises(ApiError):self.service.verify_session(result['token'])
        self.service.feishu.member_active.return_value=True
        with self.assertRaises(ApiError):self.service.verify_session(result['token'])

    def test_all_new_mail_routes_only_to_granted_members_once(self):
        self.member('ou_A');self.member('ou_B');self.member('ou_C',[self.b['id']])
        self.service.direct_notifier.set_enabled(True)
        self.assertTrue(self.insert(1));self.assertFalse(self.insert(1))
        self.assertEqual(self.store.notification_status()['counts'],{'pending':2})
        self.service.direct_notifier.run_once();self.service.direct_notifier.run_once()
        self.assertFalse(self.service.direct_notifier.run_once())
        calls=self.service.feishu.send_text.call_args_list
        self.assertEqual({c.args[0] for c in calls},{'ou_A','ou_B'})
        self.assertNotIn('body secret',calls[0].args[1])
        self.assertIn('/#/message/',calls[0].args[1])
        self.assertEqual(self.store._conn.execute('SELECT count(*) FROM message_reads').fetchone()[0],0)
        self.assertEqual(self.store.notification_status()['counts'],{'sent':2})
        self.assertFalse(self.service.direct_notifier.enqueue({'severity':'P0','action_required':True}))

    def test_old_mail_unbound_no_grants_and_disabled_accounts_do_not_leak(self):
        user=self.store.create_user('unbound',hash_password('pass-123'),account_ids=(self.a['id'],))
        self.service.direct_notifier.set_enabled(True)
        self.insert(1,ts=1)
        self.assertEqual(self.store.notification_status()['counts'],{})
        self.insert(2)
        self.assertEqual(self.store.notification_status()['counts'],{'blocked':1})
        self.store.bind_identity(self.identity()['id'],user.id,self.admin.id)
        self.assertFalse(self.service.direct_notifier.run_once())
        self.store.update_account(self.a['id'],enabled=False)
        self.insert(3)
        self.assertEqual(self.store.notification_status()['counts'],{'blocked':1})
        self.insert(4,aid=self.b['id'])
        self.assertEqual(self.store.notification_status()['counts'],{'blocked':1})

    def test_revoke_between_enqueue_and_send_cancels(self):
        user=self.member();self.service.direct_notifier.set_enabled(True);self.insert(1)
        self.store.update_user(user.id,account_ids=())
        self.service.direct_notifier.run_once()
        self.service.feishu.send_text.assert_not_called()
        self.assertEqual(self.store.notification_status()['counts'],{'skipped':1})

    def test_revoke_during_directory_check_cancels(self):
        user=self.member();self.service.direct_notifier.set_enabled(True);self.insert(1)
        def revoke(_):self.store.update_user(user.id,account_ids=());return True
        self.service.feishu.member_active.side_effect=revoke
        self.service.direct_notifier.run_once()
        self.service.feishu.send_text.assert_not_called()

    def test_restart_retries_with_same_uuid_and_no_backfill(self):
        self.member();self.service.direct_notifier.set_enabled(True);self.insert(1)
        self.service.feishu.send_text.side_effect=FeishuError('network_error')
        self.service.direct_notifier.run_once()
        uuid=self.store._conn.execute('SELECT uuid FROM notification_outbox').fetchone()[0]
        self.store._conn.execute('UPDATE notification_outbox SET next_attempt=0');self.store._conn.commit()
        self.store.close();self.service=Service.build(self.settings);self.store=self.service.store;self.wire_fakes()
        self.service.direct_notifier.run_once()
        self.assertEqual(self.service.feishu.send_text.call_args.args[2],uuid)
        self.assertEqual(self.store.notification_status()['counts'],{'sent':1})
        self.service.direct_notifier.set_enabled(False)
        self.insert(2)
        self.service.direct_notifier.set_enabled(True)
        self.assertFalse(self.service.direct_notifier.run_once())

    def test_claim_crash_recovers_and_expired_dedup_window_stops(self):
        self.member();self.service.direct_notifier.set_enabled(True);self.insert(1)
        self.store.claim_notification()
        self.store._conn.execute('UPDATE notification_outbox SET lease_until=0');self.store._conn.commit()
        self.service.direct_notifier.run_once()
        self.assertEqual(self.store.notification_status()['counts'],{'sent':1})
        self.insert(2)
        self.store._conn.execute("UPDATE notification_outbox SET first_attempt=? WHERE status='pending'",(time.time()-3601,));self.store._conn.commit()
        self.service.direct_notifier.run_once()
        self.assertEqual(self.store.notification_status()['counts'],{'sent':1,'failed':1})

    def test_http_pending_identity_cannot_access_business_and_cross_site_denied(self):
        root=Path(__file__).resolve().parents[1]
        self.server=start_http_server('127.0.0.1',0,self.service,root/'src/store_mail_web/static',root/'openapi.yaml')
        base='http://127.0.0.1:'+str(self.server.server_address[1])
        identity=self.identity();token=self.store.applicant_token(identity['id'])
        def req(path,method='GET',body=None,origin=None,cookie=None):
            headers={'Cookie':cookie or 'smw_applicant='+token}
            if origin:headers['Origin']=origin
            if body is not None:headers['Content-Type']='application/json'
            request=urllib.request.Request(base+path,method=method,headers=headers,data=json.dumps(body).encode() if body is not None else None)
            try:
                with urllib.request.urlopen(request) as r:return r.status,json.load(r)
            except urllib.error.HTTPError as e:return e.code,json.load(e)
        self.assertEqual(req('/api/auth/admission')[0],200)
        for path in ['/api/web/messages','/api/web/accounts','/api/web/dashboard','/api/web/admissions']:
            self.assertEqual(req(path)[0],401)
        self.assertEqual(req('/api/web/accounts',cookie='smw_session='+token)[0],401)
        self.assertEqual(req('/api/auth/admission','POST',{'note':'test'},'https://evil.example')[0],403)
        self.assertEqual(req('/api/auth/admission','POST',{'note':'test'},self.settings.public_url)[0],200)
        self.assertEqual(req('/api/auth/admission/complete','POST',{})[0],403)

    def test_foreign_tenant_and_missing_status_fail_closed(self):
        client=FeishuClient(self.settings)
        client._request=Mock(side_effect=[{'access_token':'fake'},{'data':{'open_id':'ou_bad','tenant_key':'foreign'}}])
        with self.assertRaises(FeishuError):client.exchange('code','verifier')
        client.tenant_token=Mock(return_value='fake')
        client._request=Mock(return_value={'data':{'user':{}}})
        with self.assertRaises(FeishuError):client.member_active('ou_X')
        text=mail_text({'id':1,'subject':'Your verification code: AB1234','display_name':'A','email':'a@example.invalid','received_at':'today'},'https://mail.test.example')
        self.assertNotIn('AB1234',text)

    def test_oauth_pkce_rfc_vector_and_token_exchange_encoding(self):
        client=FeishuClient(self.settings)
        verifier='dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk'
        url=client.authorize_url('state',verifier)
        q=urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        self.assertEqual(q['code_challenge'],['E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM'])
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return b'{"access_token":"fake"}'
        client._opener.open=Mock(return_value=Response())
        client._request(client.TOKEN_URL,{'code_verifier':verifier,'client_secret':'test+& secret'})
        req=client._opener.open.call_args.args[0]
        self.assertEqual(req.get_header('Content-type'),'application/json')
        self.assertEqual(json.loads(req.data)['code_verifier'],verifier)
        self.assertEqual(json.loads(req.data)['client_secret'],'test+& secret')

    def test_admin_explicit_notification_scope_does_not_reduce_visibility(self):
        i=self.identity('ou_root_admin');self.store.bind_identity(i['id'],self.admin.id,self.admin.id)
        self.service.direct_notifier.set_enabled(True)
        self.insert(1)
        self.assertEqual(self.store.notification_status()['counts'],{})
        self.service.update_user(self.admin.id,{'account_ids':[self.a['id']]},self.admin)
        self.insert(2)
        self.assertEqual(self.store.notification_status()['counts'],{'pending':1})
        self.assertEqual(len(self.service.accounts_view(self.store.get_user(self.admin.id))),2)
