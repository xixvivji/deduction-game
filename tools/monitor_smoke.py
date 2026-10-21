"""Isolated synthetic UI/integration check on 8201. Never writes the game DB or calls an LLM."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from contextlib import asynccontextmanager
import asyncio
import uvicorn
from game.server import create_app,DemoClient
from game.engine import Game
from game.decision import decide
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
        g=Game('synthetic-monitor',{f'p{i}':'ai' for i in range(5)},seed=1);g.players['p0'].role='doctor';g.phase='discussion'
        class Bad(DemoClient):
            def generate(self,*args):return {'text':'p2가 p1을 죽였어.','decision_summary':'합성 검증 문장','evidence_ids':[],'claim_type':'none','claim_subject':None,'claim_value':''}
        for client in (DemoClient(),Bad()):
            await asyncio.to_thread(hub.monitor.run,'demo',client,decide,g.observation('p0'),client,policy='verified',discussion_quality=False)
        try:yield
        finally:await asyncio.to_thread(exporter.close)

app.router.lifespan_context=lifespan
if __name__=='__main__':uvicorn.run(app,host='127.0.0.1',port=8201)
