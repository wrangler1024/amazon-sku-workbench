#!/usr/bin/env python3
"""Official-source monitoring and reviewed fee releases. Check never publishes."""
import argparse
import base64
import concurrent.futures
import copy
import datetime as dt
import difflib
import fcntl
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
import urllib.error
from html.parser import HTMLParser

HERE = Path(__file__).resolve().parent
RATE_PATH = Path('rates/2026-10-31.2.json')
MAX_BYTES = 16 * 1024 * 1024
MARKETS = {'AU','US','CA','MX','UK','DE','FR','IT','ES','NL','SE','PL','BE','IE','AE','SA','JP'}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


def identifier(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}', value):
        raise ValueError('Invalid identifier')
    return value


def official_url(url):
    p = urllib.parse.urlsplit(url)
    allowed = {'sellercentral.amazon.com','sellercentral.amazon.ca','sellercentral.amazon.com.au',
               'sellercentral.amazon.com.mx','sellercentral.amazon.co.uk','sell.amazon.co.uk',
               'sell.amazon.ca','m.media-amazon.com','sellercentral.amazon.ae','sellercentral.amazon.sa','sellercentral.amazon.co.jp','sell.amazon.ae','sell.amazon.sa','sell.amazon.co.jp'}
    if p.scheme != 'https' or p.hostname not in allowed or p.username or p.password or p.port not in (None,443):
        raise ValueError('Only configured public Amazon HTTPS hosts are allowed')
    return url


class OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        official_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ignore = 0
        self.parts = []
        self.links = []
        self.tables = []
        self.table = None
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script','style','noscript','svg'):
            self.ignore += 1
        if self.ignore:
            return
        if tag == 'a' and attrs.get('href'):
            self.links.append(attrs['href'])
        if tag in ('p','div','li','h1','h2','h3','h4','tr','br'):
            self.parts.append('\n')
        if tag == 'table' and self.table is None:
            self.table = []
        elif tag == 'tr' and self.table is not None:
            self.row = []
        elif tag in ('td','th') and self.row is not None:
            self.cell = []

    def handle_endtag(self, tag):
        if tag in ('script','style','noscript','svg') and self.ignore:
            self.ignore -= 1
            return
        if self.ignore:
            return
        if tag in ('td','th') and self.cell is not None:
            self.row.append(' '.join(' '.join(self.cell).split()))
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.table.append(self.row)
            self.row = None
        elif tag == 'table' and self.table is not None:
            self.tables.append(self.table)
            self.table = None
        if tag in ('p','div','li','h1','h2','h3','h4','tr'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.ignore:
            self.parts.append(data)
            if self.cell is not None:
                self.cell.append(data)


def normalize(text):
    return '\n'.join(line for line in (' '.join(s.split()) for s in text.splitlines()) if line)


def json_html(value):
    if isinstance(value, str):
        return [value] if re.search(r'<(?:table|p|div|h[123])\b', value) else []
    if isinstance(value, dict):
        return [s for v in value.values() for s in json_html(v)]
    if isinstance(value, list):
        return [s for v in value for s in json_html(v)]
    return []


def extract(source, raw):
    if source['kind'] == 'pdf':
        if not raw.startswith(b'%PDF-'):
            raise ValueError('Expected a PDF, received another response')
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw))
        if len(reader.pages) > 150:
            raise ValueError('Unexpected PDF length')
        pages = [normalize(p.extract_text() or '') for p in reader.pages]
        text = '\n'.join(f'PAGE {i+1}\n{s}' for i,s in enumerate(pages))
        tables, links = [], []
    else:
        html = raw.decode('utf-8', errors='replace')
        if source['kind'] == 'help_json':
            fragments = json_html(json.loads(html))
            if not fragments:
                raise ValueError('Official help response contains no article HTML')
            html = '\n'.join(fragments)
        parser = ArticleParser()
        parser.feed(html)
        text = normalize(''.join(parser.parts))
        tables = parser.tables
        links = sorted(set(urllib.parse.urljoin(source['url'], x) for x in parser.links
                           if '.pdf' in x.lower() and ('rate' in x.lower() or 'fba' in x.lower())))
    if len(text) < source.get('minChars', 600):
        raise ValueError('Article is missing or too short; browser/authentication may be needed')
    for pattern in source.get('requiredPatterns', []):
        if not re.search(pattern, text, re.I):
            raise ValueError('Required article content missing: ' + pattern)
    if source.get('minTables',0) > len(tables):
        raise ValueError('Required fee tables missing; dynamic page/layout needs review')
    if re.search(r'enter the characters you see|robot check|sorry, we just need to make sure', text, re.I):
        raise ValueError('Official source returned a challenge page')
    numeric = [line for line in text.splitlines() if re.search(r'\d',line)]
    canonical = {'text':text,'tables':tables,'rateDocumentLinks':links}
    return {**canonical,'numericAndRuleLines':numeric,
            'contentSha256':digest(json.dumps(canonical,ensure_ascii=False,sort_keys=True).encode())}


def fetch_source(source):
    started = now()
    try:
        url = official_url(source.get('fetchUrl',source['url']))
        payload = json.dumps(source['requestBody']).encode() if source.get('requestBody') else None
        req = urllib.request.Request(url, data=payload, headers={
            'User-Agent':'Samforo-FBA-Rate-Monitor/0.1 (+https://fbacc.samforo.icu)',
            'Accept-Language':source.get('language','en-US,en;q=0.9'),
            'Content-Type':'application/json' if payload else 'text/plain',
            'Accept':'application/pdf,application/json,text/html;q=0.9,*/*;q=0.5'})
        for attempt in range(2):
            try:
                with urllib.request.build_opener(OfficialRedirect()).open(req,timeout=18) as response:
                    raw = response.read(MAX_BYTES + 1)
                    if len(raw)>MAX_BYTES:
                        raise ValueError('Source exceeds 16 MiB limit')
                    metadata = {'httpStatus':response.status,'finalUrl':response.url,'attempts':attempt+1,
                                'contentType':response.headers.get('Content-Type','')}
                break
            except (OSError,urllib.error.URLError) as error:
                if attempt or (isinstance(error,urllib.error.HTTPError) and error.code not in (429,500,502,503,504)):
                    raise
        document = extract(source,raw)
        return {'id':source['id'],'checkedAt':started,'status':'ok','markets':source['markets'],
                'url':source['url'],'rawSha256':digest(raw),**metadata,**document}, raw
    except Exception as error:
        return {'id':source['id'],'checkedAt':started,'status':'unavailable',
                'markets':source['markets'],'url':source['url'],
                'error':f'{type(error).__name__}: {error}'[:500]}, None


def fetch_bounded(source):
    # urllib's socket timeout alone does not bound a slow streaming response.
    # Isolate each public read so a stalled source cannot hang the daily run.
    try:
        worker=subprocess.run([sys.executable,str(HERE/'pipeline.py'),'--fetch-once'],
                              input=json.dumps(source),capture_output=True,text=True,
                              timeout=120 if source['kind']=='pdf' else 55)
        if worker.returncode:
            raise ValueError('Source reader failed: '+worker.stderr[-200:])
        value=json.loads(worker.stdout)
        return value['result'],base64.b64decode(value['rawBase64']) if value['rawBase64'] is not None else None
    except Exception as error:
        return {'id':source['id'],'checkedAt':now(),'status':'unavailable','markets':source['markets'],
                'url':source['url'],'error':f'{type(error).__name__}: source read did not complete within its limit'},None


def tree_manifest(root):
    result = {}
    for path in sorted(Path(root).rglob('*')):
        if path.is_symlink():
            raise ValueError('Symlinks are not permitted in candidate workspaces')
        if path.is_file():
            result[str(path.relative_to(root))] = digest(path.read_bytes())
    return result


def immutable_manifest(root):
    return {p:h for p,h in tree_manifest(root).items()
            if p != str(RATE_PATH) and not p.startswith('tests/')}


def source_differences(previous, current):
    def lines(document):
        if not document:return []
        return document['text'].splitlines()+['RATE_DOCUMENT_LINK '+u for u in document.get('rateDocumentLinks',[])]+[
            'TABLE '+json.dumps(t,ensure_ascii=False) for t in document.get('tables',[])]
    return list(difflib.unified_diff(lines(previous),lines(current),
                                    fromfile='previous observation',tofile='current observation',n=2))


def check(args):
    config = read_json(args.config)
    state = Path(args.state)
    active = Path(args.release).resolve()
    book = read_json(active/RATE_PATH)
    old = read_json(state/'latest.json') if (state/'latest.json').exists() else {'sources':[]}
    prior = {s['id']:s for s in old['sources']}
    run_id = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    run = state/'runs'/run_id
    run.mkdir(parents=True)
    results = []
    selected = [s for s in config['sources'] if not getattr(args,'retry_failed',False)
                or prior.get(s['id'],{}).get('status')!='ok']
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        fetched = {result['id']:(result,raw) for result,raw in pool.map(fetch_bounded,selected)}
        for source in config['sources']:
            if source['id'] not in fetched:
                result=copy.deepcopy(prior[source['id']])
                result['change']='not_rechecked'
                write_json(run/f"{identifier(source['id'])}.json",result)
                results.append(result)
                continue
            result,raw=fetched[source['id']]
            source_id = identifier(result['id'])
            previous_path = state/'observed'/f'{source_id}.json'
            previous = read_json(previous_path) if previous_path.exists() else None
            if raw is not None:
                object_path = state/'objects'/f"{result['rawSha256']}.gz"
                object_path.parent.mkdir(exist_ok=True)
                if not object_path.exists():
                    object_path.write_bytes(gzip.compress(raw,mtime=0))
                result['rawObject'] = str(object_path.relative_to(state))
                result['change'] = 'first_observation' if previous is None else (
                    'unchanged' if previous['contentSha256']==result['contentSha256'] else 'changed')
                if result['change']=='unchanged' and prior.get(source_id,{}).get('status')=='unavailable':
                    result['change']='recovered'
                result['lastSuccessAt'] = result['checkedAt']
                diff = source_differences(previous,result)
                (run/f'{source_id}.diff.txt').write_text('\n'.join(diff)+'\n')
                write_json(previous_path,result)
            else:
                result['change'] = 'unknown'
                result['lastSuccessAt'] = previous.get('lastSuccessAt') if previous else None
            write_json(run/f'{source_id}.json',result)
            results.append(result)
    changed = [s for s in results if s.get('change') in ('changed','first_observation','recovered')]
    failed = [s for s in results if s['status']!='ok']
    report = {'schemaVersion':1,'runId':run_id,'checkedAt':now(),'activeRateVersion':book['version'],
              'activeRateSha256':digest((active/RATE_PATH).read_bytes()),
              'status':'attention_required' if changed or failed else 'no_observed_change',
              'sources':results,'changedSourceIds':[s['id'] for s in changed],
              'unavailableSourceIds':[s['id'] for s in failed],
              'lastReviewedAt':read_json(state/'last-reviewed.json')['approvedAt'] if (state/'last-reviewed.json').exists() else None,
              'automaticallyVerified':False}
    if changed:
        candidate = state/'candidates'/run_id
        candidate.mkdir(parents=True)
        shutil.copyfile(active/RATE_PATH,candidate/'rates.draft.json')
        candidate_sources = {'runId':run_id,'sources':results}
        write_json(candidate/'sources.json',candidate_sources)
        write_json(candidate/'candidate.json',{
            'id':run_id,'createdAt':now(),'status':'draft_needs_review',
            'baseRateVersion':book['version'],'baseRateSha256':report['activeRateSha256'],
            'sourceChanges':report['changedSourceIds'],
            'affectedMarkets':sorted({m for s in changed for m in s['markets']}),
            'sourceEvidenceSha256':digest((candidate/'sources.json').read_bytes()),
            'rateValuesAutomaticallyUpdated':False,
            'instructions':'rates.draft.json is a copy of active rates, not extracted/verified new fees. Review source differences, then edit rates/tests/samples in the prepared workspace.'})
        report['candidateId'] = run_id
    else:
        report['candidateId'] = old.get('candidateId')
    report['pendingCandidateIds'] = sorted(p.name for p in (state/'candidates').glob('*')
        if p.is_dir() and not (state/'reviews'/f'{p.name}.json').exists())
    report['pendingReview'] = bool(report['pendingCandidateIds'])
    if report['pendingReview']:
        report['status'] = 'attention_required'
    if config.get('verifyCandidates', False):
        if changed:
            candidate_args = copy.copy(args)
            candidate_args.candidate = run_id
            prepare(candidate_args)
            verify(candidate_args)
            proof = read_json(state/'candidates'/run_id/'verification.json')
            report['candidateValidation'] = proof['status']
        else:
            samples = run_checked(['node',str(HERE/'verify-samples.mjs'),str(active),str(HERE/'samples.json')],active,40)
            write_json(run/'active-sample-check.json',samples)
            report['activeSampleValidation'] = 'passed' if samples['exitCode']==0 else 'failed'
    rows = ['# FBA official-source check', '',f"Checked (UTC): {report['checkedAt']}",
            f"Active rates: {book['version']}",'',
            '| Source | Markets | Fetch | Change |','|---|---|---|---|']
    rows.extend(f"| {s['id']} | {','.join(s['markets'])} | {s['status']} | {s['change']} |" for s in results)
    rows.extend(['','A successful download is not an approval of current fees.',
                 'Candidate drafts retain active values until source review; scheduled checks never publish.',
                 f"Candidate: {report.get('candidateId') or 'none'}"])
    (run/'report.md').write_text('\n'.join(rows)+'\n')
    write_json(run/'report.json',report)
    write_json(state/'latest.json',report)
    print(json.dumps({k:report[k] for k in ('runId','status','candidateId','changedSourceIds','unavailableSourceIds')},ensure_ascii=False))
    return 2 if failed or report.get('candidateValidation')=='blocked' or report.get('activeSampleValidation')=='failed' else 0


def candidate_dir(args):
    path = Path(args.state)/'candidates'/identifier(args.candidate)
    if not path.is_dir() or path.is_symlink():
        raise ValueError('Candidate does not exist')
    return path


def prepare(args):
    candidate = candidate_dir(args)
    meta = read_json(candidate/'candidate.json')
    active = Path(args.release).resolve()
    if digest((active/RATE_PATH).read_bytes())!=meta['baseRateSha256']:
        raise ValueError('Active fee snapshot changed; create a fresh candidate')
    workspace = candidate/'workspace'
    if workspace.exists():
        raise ValueError('Workspace already exists; existing review edits are preserved')
    shutil.copytree(active,workspace)
    # Releases are read-only for service users; only the isolated copy is made writable.
    for p in [workspace,*workspace.rglob('*')]:
        if p.is_symlink():
            raise ValueError('Active release contains an unexpected symlink')
        p.chmod(0o750 if p.is_dir() else 0o640)
    shutil.copyfile(candidate/'rates.draft.json',workspace/RATE_PATH)
    shutil.copyfile(HERE/'samples.json',candidate/'samples.json')
    meta['immutableCodeManifest'] = immutable_manifest(active)
    meta['status'] = 'prepared_needs_review'
    write_json(candidate/'candidate.json',meta)
    print(json.dumps({'candidate':args.candidate,'workspace':str(workspace),'status':meta['status']}))


def json_diff(before,after,path=''):
    if type(before)!=type(after):
        return [{'path':path,'before':before,'after':after}]
    if isinstance(before,dict):
        return [item for key in sorted(before.keys()|after.keys())
                for item in json_diff(before.get(key),after.get(key),path+'/'+key)]
    if isinstance(before,list):
        if len(before)!=len(after):return [{'path':path,'before':before,'after':after}]
        return [item for i,(a,b) in enumerate(zip(before,after)) for item in json_diff(a,b,path+'/'+str(i))]
    return [] if before==after else [{'path':path,'before':before,'after':after}]


def run_checked(command,cwd,timeout):
    result = subprocess.run(command,cwd=cwd,text=True,capture_output=True,timeout=timeout)
    return {'command':command,'exitCode':result.returncode,'stdout':result.stdout[-60000:],'stderr':result.stderr[-10000:]}


def verify(args):
    candidate = candidate_dir(args)
    meta = read_json(candidate/'candidate.json')
    workspace = candidate/'workspace'
    active = Path(args.release).resolve()
    if digest((active/RATE_PATH).read_bytes())!=meta['baseRateSha256']:
        raise ValueError('Active snapshot changed since candidate creation')
    if immutable_manifest(workspace)!=meta['immutableCodeManifest'] or immutable_manifest(active)!=meta['immutableCodeManifest']:
        raise ValueError('Candidate application/deployment code differs from active release; use normal code release review')
    if digest((candidate/'sources.json').read_bytes())!=meta['sourceEvidenceSha256']:
        raise ValueError('Source evidence changed after capture')
    before,after = read_json(active/RATE_PATH),read_json(workspace/RATE_PATH)
    if set(after['markets'])!=MARKETS:
        raise ValueError('Candidate must retain all 17 marketplaces')
    differences = json_diff(before,after)
    changed_markets = sorted(m for m in MARKETS if before['markets'][m]!=after['markets'][m])
    if differences and (after['version']==before['version'] or not changed_markets):
        raise ValueError('Rate changes require a new version and explicit affected marketplaces')
    for market in changed_markets:
        m=after['markets'][market]
        if m['version']!=after['version'] or m['verifiedAt']<before['markets'][market]['verifiedAt']:
            raise ValueError('Affected market version/verification date must be updated')
        dt.date.fromisoformat(m['verifiedAt']); dt.date.fromisoformat(m['effectiveFrom'])
    sources = read_json(candidate/'sources.json')['sources']
    relevant = [s for s in sources if set(s['markets']) & set(meta['affectedMarkets']+changed_markets)]
    unavailable = [s['id'] for s in relevant if s['status']!='ok']
    checks=[]
    checks.append(run_checked(['node','scripts/check-rates.mjs'],workspace,30))
    test_files=sorted(str(p.relative_to(workspace)) for p in (workspace/'tests').glob('*.test.mjs'))
    checks.append(run_checked(['node','--test','--test-concurrency=1',*test_files],workspace,120))
    checks.append(run_checked(['node',str(HERE/'verify-samples.mjs'),str(workspace),str(candidate/'samples.json')],workspace,30))
    passed=not unavailable and all(c['exitCode']==0 for c in checks)
    # Independent reviewed samples are required for every market, plus boundary
    # samples for changed markets; do not generate expectations from the engine.
    samples=read_json(candidate/'samples.json')['samples']
    coverage={m:[s for s in samples if s['request']['marketplace']==m] for m in MARKETS}
    if any(not v for v in coverage.values()):passed=False
    missing_boundaries=[m for m in changed_markets if len(coverage[m])<3 or not any(s.get('boundary') for s in coverage[m])]
    if missing_boundaries:passed=False
    test_summary=checks[1]['stdout']
    count=re.search(r'(?:#|ℹ) tests (\d+)',test_summary)
    if not count or int(count.group(1))<49:passed=False
    verification={'verifiedAt':now(),'status':'passed' if passed else 'blocked',
                  'rateVersion':after['version'],'rateDifferences':differences,'changedMarkets':changed_markets,
                  'unavailableRequiredSources':unavailable,'missingBoundarySamples':missing_boundaries,
                  'checks':checks,'workspaceManifest':tree_manifest(workspace),
                  'samplesSha256':digest((candidate/'samples.json').read_bytes()),
                  'sourceEvidenceSha256':meta['sourceEvidenceSha256'],
                  'baseRateSha256':meta['baseRateSha256'],
                  'note':'Tests use reviewed reference amounts; this is not a live Amazon quotation API.'}
    write_json(candidate/'verification.json',verification)
    print(json.dumps({k:verification[k] for k in ('status','rateVersion','changedMarkets','unavailableRequiredSources','missingBoundarySamples')}))
    return 0 if passed else 2


def validate_receipt_inputs(candidate,active):
    meta=read_json(candidate/'candidate.json')
    proof=read_json(candidate/'verification.json')
    if proof['status']!='passed':raise ValueError('Verification has not passed')
    if digest((active/RATE_PATH).read_bytes())!=proof['baseRateSha256']:raise ValueError('Active rates changed')
    if tree_manifest(candidate/'workspace')!=proof['workspaceManifest']:raise ValueError('Workspace modified after verification')
    if digest((candidate/'samples.json').read_bytes())!=proof['samplesSha256']:raise ValueError('Samples modified after verification')
    if digest((candidate/'sources.json').read_bytes())!=proof['sourceEvidenceSha256']:raise ValueError('Source evidence modified')
    if immutable_manifest(active)!=meta['immutableCodeManifest']:raise ValueError('Active application code changed')
    if immutable_manifest(candidate/'workspace')!=meta['immutableCodeManifest']:
        raise ValueError('Candidate application/deployment code differs from the active release')
    latest_path=candidate.parent.parent/'latest.json'
    if latest_path.exists():
        latest={s['id']:s for s in read_json(latest_path)['sources']}
        for source in read_json(candidate/'sources.json')['sources']:
            current=latest.get(source['id'])
            if not current or current['status']!='ok' or current.get('contentSha256')!=source.get('contentSha256'):
                raise ValueError('Latest source check differs or is incomplete; review a fresh candidate')
    # A month-old download must not pass as a fresh source review.
    if dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(proof['verifiedAt'])>dt.timedelta(days=7):
        raise ValueError('Verification is older than seven days; verify again')
    for source in read_json(candidate/'sources.json')['sources']:
        if source['status']=='ok' and dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(source['checkedAt'])>dt.timedelta(days=7):
            raise ValueError('Source evidence is older than seven days; run a fresh check')
    return proof


def approve(args):
    if os.geteuid()!=0:raise PermissionError('Approval requires an administrator; scheduled user cannot approve')
    if not args.reviewer.strip() or len(args.note.strip())<8:raise ValueError('Reviewer and meaningful review note required')
    candidate=candidate_dir(args)
    proof=validate_receipt_inputs(candidate,Path(args.release).resolve())
    receipt={'candidateId':args.candidate,'reviewer':args.reviewer,'reviewNote':args.note,
             'approvedAt':now(),'verificationSha256':digest((candidate/'verification.json').read_bytes()),
             'workspaceManifest':proof['workspaceManifest'],'rateVersion':proof['rateVersion']}
    directory=Path(args.approvals)
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    if directory.stat().st_uid!=0 or directory.stat().st_mode & 0o022:raise PermissionError('Approval directory must be root-owned and not writable by others')
    target=directory/f'{identifier(args.candidate)}.json'
    if target.exists():raise ValueError('Approval already exists; do not overwrite audit records')
    write_json(target,receipt);target.chmod(0o600)
    # Public status mirrors are informational only; publish trusts the root-only receipt.
    write_json(Path(args.state)/'reviews'/f'{args.candidate}.json',receipt)
    write_json(Path(args.state)/'last-reviewed.json',receipt)
    print(json.dumps({'candidateId':args.candidate,'status':'approved','published':False}))


def publish(args):
    if os.geteuid()!=0:raise PermissionError('Publishing requires an administrator')
    candidate=candidate_dir(args)
    active=Path(args.release).resolve()
    proof=validate_receipt_inputs(candidate,active)
    receipt=read_json(Path(args.approvals)/f'{identifier(args.candidate)}.json')
    if receipt['verificationSha256']!=digest((candidate/'verification.json').read_bytes()):raise ValueError('Verification changed after approval')
    if receipt['workspaceManifest']!=proof['workspaceManifest']:raise ValueError('Approval does not match candidate')
    if not proof['rateDifferences']:raise ValueError('No fee changes to publish; retain the current release')
    release_id=identifier('rates-'+proof['rateVersion']+'-'+args.candidate)
    # Freeze a private administrator copy; tar never follows mutable candidate links.
    with tempfile.TemporaryDirectory(prefix='fba-reviewed-publish-') as temp:
        frozen=Path(temp)/'release'
        shutil.copytree(candidate/'workspace',frozen,symlinks=True)
        if tree_manifest(frozen)!=receipt['workspaceManifest']:raise ValueError('Candidate changed during freeze')
        archive=Path(temp)/'release.tar.gz'
        with tarfile.open(archive,'w:gz') as tf:
            for p in sorted(frozen.iterdir()):tf.add(p,arcname=p.name,recursive=True)
        sha=digest(archive.read_bytes())
        result=run_checked(['bash',str(active/'deploy/linux/install-release.sh'),str(archive),release_id,sha],active,240)
        record={'publishedAt':now(),'releaseId':release_id,'archiveSha256':sha,'result':result}
        write_json(Path(args.approvals)/f'{args.candidate}.publication.json',record)
        print(json.dumps({'release':release_id,'exitCode':result['exitCode']}))
        return result['exitCode']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',default='/var/lib/fba-rate-monitor')
    parser.add_argument('--release',default='/opt/fba-fee-calculator/current')
    parser.add_argument('--config',default=str(HERE/'sources.json'))
    parser.add_argument('--approvals',default='/etc/fba-rate-monitor/approvals')
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('check').add_argument('--retry-failed',action='store_true',help='Retry only unavailable sources; preserve other sources original check times')
    commands.add_parser('status')
    for action in ('prepare','verify','approve','publish'):
        sub=commands.add_parser(action);sub.add_argument('--candidate',required=True)
        if action=='approve':sub.add_argument('--reviewer',required=True);sub.add_argument('--note',required=True)
    args=parser.parse_args()
    state=Path(args.state);state.mkdir(parents=True,exist_ok=True)
    with (state/'.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if args.command=='status':
            latest=read_json(state/'latest.json')
            print(json.dumps({k:v for k,v in latest.items() if k!='sources'},ensure_ascii=False,indent=2))
            return 0
        return globals()[args.command](args) or 0


if __name__=='__main__':
    try:
        if sys.argv[1:]==['--fetch-once']:
            result,raw=fetch_source(json.load(sys.stdin))
            print(json.dumps({'result':result,'rawBase64':base64.b64encode(raw).decode() if raw is not None else None}))
            sys.exit(0)
        sys.exit(main())
    except Exception as error:
        print(json.dumps({'status':'error','type':type(error).__name__,'message':str(error)},ensure_ascii=False),file=sys.stderr)
        sys.exit(1)
