"""Safe aggregates from immutable evaluation artifacts; missing and stale data stay explicit."""
import hashlib
import json
from pathlib import Path

METRICS = ('runs','correct','model_calls','research_calls','fallbacks','false_blocks','scope_violations',
           'mean_ms','p95_ms','input_tokens','output_tokens','usage_unknown_calls')


def read_report(path):
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else None
    except (OSError, ValueError): return None


def source_current(root, report):
    hashes = {k:v for k,v in report.get('source_sha256', {}).items() if k in ('game/decision.py','game/mcp_memory.py')}
    return bool(hashes) and all(hashlib.sha256((root/p).read_bytes()).hexdigest() == value for p,value in hashes.items())


def comparison(root, prefix):
    before = read_report(root/'reports'/f'{prefix}-before-v1.json')
    after = read_report(root/'reports'/f'{prefix}-after-v1.json')
    if not before or not after: return {'status':'not_measured'}
    try:
        b,a = before['decisions'],after['decisions']
        if b['fixture_sha256'] != a['fixture_sha256'] or b['config'] != a['config']:
            return {'status':'incomparable'}
        config = a['config']
        result = {'status':'measured', 'source_current':source_current(root, after),
                  'created_at':after['created_at'], 'fixture_sha256':a['fixture_sha256'],
                  'config':{k:config[k] for k in ('kind','repeats','provider','model','backend') if k in config},
                  'before':{k:b['summary'].get(k) for k in METRICS},
                  'after':{k:a['summary'].get(k) for k in METRICS}}
        if 'mcp' in before and 'mcp' in after:
            if before['mcp']['window_seconds'] != after['mcp']['window_seconds']:
                result['mcp'] = {'status':'incomparable'}
            else:
                result['mcp'] = {'status':'measured', 'window_seconds':after['mcp']['window_seconds'],
                    **{mode:{k:report['mcp']['summary'][k] for k in ('trials','recovered','failed_requests','mean_recovery_seconds')}
                       for mode,report in (('before',before),('after',after))}}
        return result
    except (KeyError, TypeError, OSError): return {'status':'invalid_report'}


def quality_fingerprint(root):
    paths = sorted([*root.glob('game/*.py'), *root.glob('tests/*.py'), *root.glob('tests/*.cjs'),
                    *root.glob('frontend/assets/*.js'), *root.glob('tools/run_checks.py'), *root.glob('tools/reliability_probe.py'),
                    *root.glob('.github/workflows/*.yml')])
    hasher = hashlib.sha256()
    for p in paths:
        hasher.update(str(p.relative_to(root)).encode()); hasher.update(p.read_bytes())
    return hasher.hexdigest()


def load(root):
    checks = read_report(root/'reports/reliability-checks-v1.json')
    quality = {'status':'not_measured'}
    if checks:
        quality = {'status':checks.get('status','invalid_report'), 'source':checks.get('source','local'),
                   'created_at':checks.get('created_at'), 'source_current':checks.get('source_sha256') == quality_fingerprint(root),
                   'checks':[{k:r.get(k) for k in ('name','passed','tests','seconds')} for r in checks.get('checks',[])],
                   'github_run_url':checks.get('github_run_url')}
    return {'scripted':comparison(root,'reliability'), 'live':comparison(root,'reliability-live'), 'quality':quality}


class ReportStore:
    """Reload completed CLI reports on the next ops poll, without re-running an evaluation."""
    def __init__(self, root):
        self.root = root
        self.signature = None
        self.value = None

    def snapshot(self):
        paths = sorted((self.root/'reports').glob('reliability*-v1.json'))
        stamp = tuple((p.name,p.stat().st_mtime_ns,p.stat().st_size) for p in paths)
        if self.value is None or stamp != self.signature:
            self.value = load(self.root)
            self.signature = stamp
        return self.value
