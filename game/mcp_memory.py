"""One persistent stdio MCP session, owned and closed by one asyncio task in a worker thread."""
import asyncio
from concurrent.futures import Future
import json
import os
from pathlib import Path
import queue
import secrets
import sys
import threading
from datetime import timedelta
from langsmith import traceable
from .time_budget import remaining
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from .memory_backend import scoped, signature


class MemoryUnavailable(RuntimeError):pass


class MCPMemory:
    name='mcp'
    def __init__(self):
        self.secret=secrets.token_urlsafe(32);self.jobs=queue.Queue(maxsize=16);self.ready=Future();self.closed=False
        self.thread=threading.Thread(target=self._run,daemon=True,name='game-mcp-memory');self.thread.start()
        try:self.ready.result(timeout=20)
        except Exception:
            self.close();raise MemoryUnavailable('MCP 기억 서버를 시작하지 못했습니다') from None

    def _run(self):
        try:asyncio.run(self._serve())
        except BaseException as error:
            if not self.ready.done():self.ready.set_exception(error)
            self.closed=True
            while not self.jobs.empty():
                job=self.jobs.get_nowait()
                if job and not job[-1].done():job[-1].set_exception(MemoryUnavailable('MCP 연결이 종료되었습니다'))

    async def _serve(self):
        params=StdioServerParameters(command=sys.executable,args=['-m','game.mcp_memory_server'],cwd=str(Path(__file__).resolve().parents[1]),env={'GAME_MEMORY_SECRET':self.secret, 'GAME_MEMORY_TOKEN_CACHE':os.environ.get('GAME_MEMORY_TOKEN_CACHE','false')})
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=10)) as session:
                await session.initialize()
                tools=await session.list_tools()
                required={'register_context','search_memory','release_context'}
                if not required <= {t.name for t in tools.tools}:raise MemoryUnavailable('MCP 도구 누락')
                rules=await session.read_resource('game://rules/evidence')
                self.rules='\n'.join(c.text for c in rules.contents if hasattr(c,'text'))
                self.ready.set_result(True)
                async def call(name,arguments):
                    result=await session.call_tool(name,arguments)
                    if result.isError:raise MemoryUnavailable('MCP 기억 도구 실행 실패')
                    return result.structuredContent or json.loads(next(c.text for c in result.content if c.type=='text'))
                while True:
                    job=await asyncio.to_thread(self.jobs.get)
                    if job is None:return
                    view,query,expanded,future=job
                    if future.cancelled():continue
                    token=None
                    try:
                        registered=await call('register_context',{'view':view,'proof':signature(view,self.secret)})
                        token=registered['capability']
                        result=await call('search_memory',{'capability':token,'query':query or '', 'expanded':expanded})
                        result['evidence_rules']=self.rules
                        if not future.done():future.set_result(result)
                    except Exception:
                        if not future.done():future.set_exception(MemoryUnavailable('MCP 검색에 실패했습니다. 게임을 재개해 다시 시도하세요.'))
                    finally:
                        if token:
                            try:await call('release_context',{'capability':token})
                            except Exception:pass

    @traceable(name='mcp_memory_search',run_type='retriever',process_inputs=lambda d:{'query':d.get('query'),'expanded':d.get('expanded',False),'actor':d.get('view',{}).get('self',{}).get('id')})
    def search(self,view,query=None,expanded=False):
        if self.closed:raise MemoryUnavailable('MCP 연결이 닫혔습니다')
        timeout=remaining(35)
        future=Future()
        try:self.jobs.put_nowait((scoped(view),query,expanded,future))
        except queue.Full:raise MemoryUnavailable("MCP 검색 대기열이 가득 찼습니다. 잠시 후 재개하세요.") from None
        try:return future.result(timeout=timeout)
        except TimeoutError:
            future.cancel();raise MemoryUnavailable('MCP 검색 시간 초과') from None

    def close(self):
        if not self.closed:
            self.closed=True
            while True:
                try:
                    job=self.jobs.get_nowait()
                    if job and not job[-1].done():job[-1].set_exception(MemoryUnavailable('MCP 종료로 검색을 취소했습니다'))
                except queue.Empty:break
            self.jobs.put_nowait(None)
        if threading.current_thread()!=self.thread:self.thread.join(timeout=15)
