"""Read-only localhost operations API. No prompts, role maps or raw room logs."""
from collections import Counter
import json
import os
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse

LOCAL={'127.0.0.1','::1','localhost'}


def require_local(request):
    # Reject forwarded requests: the local dashboard is not a remote admin interface.
    if (request.client is None or request.client.host not in LOCAL
            or request.url.hostname not in LOCAL
            or any(h in request.headers for h in ('forwarded','x-forwarded-for','x-forwarded-host'))):
        raise HTTPException(403,'운영 화면은 로컬에서만 사용할 수 있습니다')
    origin=request.headers.get('origin')
    if origin and origin!=f'{request.url.scheme}://{request.url.netloc}':raise HTTPException(403,'허용되지 않은 요청 출처입니다')
    if request.headers.get('sec-fetch-site')=='cross-site':raise HTTPException(403,'허용되지 않은 요청 출처입니다')


def evaluation(root):
    report=json.loads((root/'reports/claim-guard-reviewed-v2.json').read_text())
    return {'title':'문장 범위 검증 v2','kind':'합성 고정 출력 재생','cases':22,
            'before':report['before']['summary'],'after':report['after']['summary'],
            'baseline':report['baseline_commit'],'scope':'생성 24건 중 조건 위반 2건 제외. 구현 에이전트 검수. 실제 게임 승률·API 지연 평가가 아님.'}


def install(app, root):
    @app.get('/ops')
    async def page(request:Request):
        require_local(request)
        return FileResponse(root/'frontend/ops.html',headers={'Cache-Control':'no-store'})

    @app.get('/api/ops')
    async def snapshot(request:Request):
        require_local(request)
        hub=request.app.state.hub
        result=hub.monitor.snapshot()
        statuses=Counter(r.data['status'] for r in hub.rooms.values())
        result.update(ready=bool(request.app.state.ready),rooms=dict(statuses),
                      inference={'active':hub.inference.active,'waiting':hub.inference.waiters,'capacity':hub.settings.concurrency,'max_waiters':hub.inference.max_waiters},
                      memory={'backend':hub.memory.name,'closed':bool(getattr(hub.memory,'closed',False)),
                              'token_cache_enabled':os.environ.get('GAME_MEMORY_TOKEN_CACHE','false').lower()=='true'},
                      limits={'decision_seconds':hub.settings.decision_seconds,'context_bytes':hub.settings.context_budget.max_bytes},
                      evaluation=app.state.ops_evaluation,
                      tracing={'langsmith_configured':os.environ.get('LANGSMITH_TRACING','').lower()=='true'})
        return result

    app.state.ops_evaluation=evaluation(root)
