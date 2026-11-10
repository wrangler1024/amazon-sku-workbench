import argparse
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1]/'pipeline.py'
spec=importlib.util.spec_from_file_location('pipeline',MODULE)
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)

class MonitoringTests(unittest.TestCase):
    def source(self):
        return {'id':'ca','url':'https://sellercentral.amazon.ca/help/hub/reference/external/GABBX6GZPA8MSZGW',
                'kind':'html','markets':['CA'],'minChars':10,'requiredPatterns':['FBA'],'minTables':1}

    def document(self,fee='4.90'):
        return f'<h1>FBA fees</h1><p>Valid from 2026-10-31</p><table><tr><th>Weight</th><th>Fee</th></tr><tr><td>100 g</td><td>{fee}</td></tr></table>'.encode()

    def test_dynamic_shell_is_not_a_successful_rate_fetch(self):
        with self.assertRaises(ValueError):p.extract(self.source(),b'<html>Please enable JavaScript. FBA</html>')

    def test_tracking_scripts_do_not_change_content_hash(self):
        a=p.extract(self.source(),self.document()+b'<script>random=100</script>')
        b=p.extract(self.source(),self.document()+b'<script>random=200</script>')
        self.assertEqual(a['contentSha256'],b['contentSha256'])
        self.assertEqual(a['tables'][0][1],['100 g','4.90'])

    def test_price_and_rule_changes_appear_in_diff(self):
        a=p.extract(self.source(),self.document())
        b=p.extract(self.source(),self.document('5.10').replace(b'100 g',b'150 g'))
        self.assertNotEqual(a['contentSha256'],b['contentSha256'])
        text='\n'.join(p.source_differences(a,b))
        self.assertIn('5.10',text);self.assertIn('150 g',text)

    def test_pdf_url_replacement_is_observed(self):
        s={**self.source(),'minTables':0}
        a=p.extract(s,b'<p>FBA pricing current rates</p><a href="https://m.media-amazon.com/old-FBA-rate.pdf">Rate card</a>')
        b=p.extract(s,b'<p>FBA pricing current rates</p><a href="https://m.media-amazon.com/new-FBA-rate.pdf">Rate card</a>')
        self.assertNotEqual(a['contentSha256'],b['contentSha256'])

    def test_json_article_html_is_extracted_without_running_scripts(self):
        s={**self.source(),'kind':'help_json'}
        raw=json.dumps({'topic':{'content':self.document().decode()},'other':'metadata'}).encode()
        self.assertIn('4.90',p.extract(s,raw)['text'])

    def test_public_source_redirect_cannot_use_credentials_or_unknown_host(self):
        for url in ['http://sellercentral.amazon.ca/x','https://evil.example/','https://name:password@sellercentral.amazon.ca/x','https://sellercentral.amazon.ca:8443/x']:
            with self.assertRaises(ValueError):p.official_url(url)

    def test_symlink_is_rejected_in_release_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'real').write_text('test');(root/'alias').symlink_to(root/'real')
            with self.assertRaises(ValueError):p.tree_manifest(root)

    def test_monitor_creates_draft_without_changing_active_rates(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);active=root/'release';(active/'rates').mkdir(parents=True)
            rate=active/p.RATE_PATH;rate.write_text(json.dumps({'version':'baseline','markets':{'CA':{'fee':490}}}))
            config=root/'sources.json';p.write_json(config,{'sources':[self.source()]})
            args=argparse.Namespace(config=str(config),state=str(root/'state'),release=str(active))
            extracted=p.extract(self.source(),self.document())
            result={'id':'ca','status':'ok','checkedAt':p.now(),'markets':['CA'],'url':self.source()['url'],'rawSha256':p.digest(self.document()),**extracted}
            before=rate.read_bytes()
            with patch.object(p,'fetch_bounded',return_value=(result,self.document())):self.assertEqual(p.check(args),0)
            latest=p.read_json(root/'state/latest.json');candidate=root/'state/candidates'/latest['candidateId']
            self.assertEqual((candidate/'rates.draft.json').read_bytes(),before)
            self.assertFalse(p.read_json(candidate/'candidate.json')['rateValuesAutomaticallyUpdated'])
            self.assertEqual(rate.read_bytes(),before)
            with patch.object(p,'fetch_bounded',return_value=(copy.deepcopy(result),self.document())):self.assertEqual(p.check(args),0)
            repeated=p.read_json(root/'state/latest.json')
            self.assertEqual(repeated['changedSourceIds'],[])
            self.assertTrue(repeated['pendingReview'])
            self.assertEqual(repeated['status'],'attention_required')
            failed={'id':'ca','status':'unavailable','checkedAt':p.now(),'markets':['CA'],'url':self.source()['url'],'error':'timeout'}
            with patch.object(p,'fetch_bounded',return_value=(failed,None)):self.assertEqual(p.check(args),2)
            last=p.read_json(root/'state/latest.json')['sources'][0]
            self.assertEqual(last['lastSuccessAt'],result['checkedAt'])
            self.assertEqual(last['status'],'unavailable')
            self.assertEqual(rate.read_bytes(),before)

    def test_scheduler_user_cannot_approve_or_publish(self):
        with patch.object(p.os,'geteuid',return_value=1234):
            with self.assertRaises(PermissionError):p.approve(argparse.Namespace())
            with self.assertRaises(PermissionError):p.publish(argparse.Namespace())

    def test_stalled_source_is_reported_without_an_unbounded_worker(self):
        with patch.object(p.subprocess,'run',side_effect=p.subprocess.TimeoutExpired('reader',55)):
            result,raw=p.fetch_bounded(self.source())
        self.assertEqual(result['status'],'unavailable')
        self.assertIsNone(raw)
        self.assertIn('TimeoutExpired',result['error'])

    def test_verified_workspace_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);active=root/'release';candidate=root/'candidate';workspace=candidate/'workspace'
            for r in [active,workspace]:(r/'rates').mkdir(parents=True);(r/p.RATE_PATH).write_text('{"version":"v1"}');(r/'app.mjs').write_text('safe')
            p.write_json(candidate/'samples.json',{});p.write_json(candidate/'sources.json',{'sources':[]})
            p.write_json(candidate/'candidate.json',{'immutableCodeManifest':p.immutable_manifest(active)})
            p.write_json(candidate/'verification.json',{'status':'passed','verifiedAt':p.now(),'baseRateSha256':p.digest((active/p.RATE_PATH).read_bytes()),'workspaceManifest':p.tree_manifest(workspace),'samplesSha256':p.digest((candidate/'samples.json').read_bytes()),'sourceEvidenceSha256':p.digest((candidate/'sources.json').read_bytes())})
            p.validate_receipt_inputs(candidate,active)
            (workspace/'app.mjs').write_text('changed')
            with self.assertRaises(ValueError):p.validate_receipt_inputs(candidate,active)
            # A writable test report cannot authorize a changed deployment unit/code.
            forged=p.read_json(candidate/'verification.json')
            forged['workspaceManifest']=p.tree_manifest(workspace)
            p.write_json(candidate/'verification.json',forged)
            with self.assertRaises(ValueError):p.validate_receipt_inputs(candidate,active)

if __name__=='__main__':unittest.main()
