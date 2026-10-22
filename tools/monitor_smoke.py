"""Isolated synthetic UI/integration check on 8201. Never writes the game DB or calls an LLM."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from contextlib import asynccontextmanager, nullcontext
from unittest.mock import patch
import asyncio
import uvicorn
from game.server import create_app,DemoClient
from game.engine import Game, RuleError
from game.mlflow_tracking import RuntimeExporter

app=create_app(':memory:',DemoClient,auto_workers=False)
original=app.router.lifespan_context

@asynccontextmanager
async def lifespan(app):
    async with original(app):
        hub=app.state.hub
        hub.monitor.source='합성 입력 운영 점검 · 규칙봇 · 실제 게임 통계 아님'
        exporter=RuntimeExporter('http://127.0.0.1:5500',experiment_name='night-testimony-monitor-smoke')
        hub.monitor.exporter=exporter
        class Bad(DemoClient):
            def generate(self,*args):return {'text':'p2가 p1을 죽였어.','decision_summary':'합성 검증 문장','evidence_ids':[],'claim_type':'none','claim_subject':None,'claim_value':''}
        for mode in ('normal', 'fallback', 'rule_rejected', 'storage_failed'):
            room, _ = hub.create('합성 점검', 5)
            room.game = Game('synthetic-monitor', {f'p{i}':'ai' for i in range(5)}, seed=1)
            room.game.players['p0'].role='doctor';room.game.phase='discussion'
            room.data.update(status='running',backend='demo')
            room.client = Bad() if mode=='fallback' else DemoClient()
            hub.queue(room)
            failure = (patch.object(room.game,'submit',side_effect=RuleError('synthetic rule failure')) if mode=='rule_rejected'
                       else patch.object(hub,'save',side_effect=OSError('synthetic storage failure')) if mode=='storage_failed' else nullcontext())
            try:
                with failure, hub.monitor.actions.track('demo') as span:
                    await hub.execute_ai_action(room,'p0',room.game.observation('p0'),span)
            except (RuleError,OSError):pass
            room.data['status']='paused'
        try:yield
        finally:await asyncio.to_thread(exporter.close)

app.router.lifespan_context=lifespan
if __name__=='__main__':uvicorn.run(app,host='127.0.0.1',port=8201)
