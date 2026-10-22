"""Run the same offline quality gate locally and in CI; retain logs outside Git."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from game.evaluation_reports import quality_fingerprint

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('runtime/verification/quality-gate.json'))
    args = parser.parse_args()
    commands = [('Python 단위·통합 검사', [sys.executable,'-m','unittest','discover','-s','tests','-v']),
                ('JavaScript 이동·화면 검사', ['node','--test','--test-reporter=tap',*[str(p.relative_to(ROOT)) for p in sorted((ROOT/'tests').glob('*.test.cjs'))]]),
                ('운영 화면 문법 검사', ['node','--check','frontend/assets/ops.js'])]
    log_dir = ROOT/'runtime/verification/check-logs'; log_dir.mkdir(parents=True,exist_ok=True)
    rows = []; env = dict(os.environ, LANGSMITH_TRACING='false', GAME_PROVIDER='demo')
    # Test fixtures must never reach billable model providers, even on a developer machine.
    for key in ('OPENAI_API_KEY','GEMINI_API_KEY','GOOGLE_API_KEY','LANGSMITH_API_KEY'): env[key] = ''
    for index,(name,command) in enumerate(commands):
        started = time.perf_counter()
        try:
            result = subprocess.run(command,cwd=ROOT,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
            output = result.stdout; passed = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired): output = 'Check could not finish'; passed = False
        (log_dir/f'{index}.log').write_text(output)
        match = re.search(r'Ran (\d+) tests',output) or re.search(r'# tests (\d+)',output)
        row = {'name':name,'passed':passed,'tests':int(match[1]) if match else None,'seconds':round(time.perf_counter()-started,3)}
        rows.append(row); print(json.dumps(row,ensure_ascii=False),flush=True)
        if not passed: print(output[-6000:],flush=True)
    ci = os.environ.get('GITHUB_ACTIONS') == 'true'
    report = {'schema_version':1,'status':'passed' if all(r['passed'] for r in rows) else 'failed',
              'created_at':datetime.now(timezone.utc).isoformat(), 'source':'github_actions' if ci else 'local',
              'source_sha256':quality_fingerprint(ROOT), 'checks':rows,
              'github_run_url':f"https://github.com/{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/{os.environ.get('GITHUB_RUN_ID')}" if ci else None}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return 0 if report['status']=='passed' else 1


if __name__ == '__main__': sys.exit(main())
