"""One bounded queue and one owning task; reconnect stdio without sharing game context."""
import asyncio
from concurrent.futures import Future, InvalidStateError
from datetime import timedelta
import json
import os
from pathlib import Path
import queue
import secrets
import sys
import threading
import time

from langsmith import traceable
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from .time_budget import remaining
from .memory_backend import scoped, signature


class MemoryUnavailable(RuntimeError): pass
class MemoryToolError(MemoryUnavailable): pass


class MCPMemory:
    name = 'mcp'

    def __init__(self, *, ping_interval=2., reconnect_limit=2, backoff=.25, cooldown=10.):
        if ping_interval <= 0 or reconnect_limit < 1 or backoff < 0 or cooldown <= 0:
            raise ValueError('MCP recovery settings must be positive')
        self.jobs = queue.Queue(maxsize=16)
        self.ready = Future()
        self.closed = False
        self.healthy = False
        self.state_lock = threading.Lock()
        self.ping_interval, self.reconnect_limit = ping_interval, reconnect_limit
        self.backoff, self.cooldown = backoff, cooldown
        self._pending = None
        self._pending_retries = 0
        self._stats = {'state':'starting', 'failures':0, 'recoveries':0, 'reconnect_attempts':0,
                       'tool_errors':0, 'last_success_at':None, 'last_recovery_seconds':None}
        self.thread = threading.Thread(target=self._run, daemon=True, name='game-mcp-memory')
        self.thread.start()
        try: self.ready.result(timeout=20)
        except Exception:
            self.close()
            raise MemoryUnavailable('MCP 기억 서버를 시작하지 못했습니다') from None

    @staticmethod
    def _finish(future, *, result=None, error=None):
        try:
            if error is None: future.set_result(result)
            else: future.set_exception(error)
        except InvalidStateError: pass

    def _drain_locked(self, message):
        while True:
            try: job = self.jobs.get_nowait()
            except queue.Empty: return
            if job is not None: self._finish(job[-1], error=MemoryUnavailable(message))

    def _status(self, **values):
        with self.state_lock: self._stats.update(values)

    def snapshot(self):
        with self.state_lock:
            return {**self._stats, 'healthy':self.healthy, 'closed':self.closed,
                    'pending':self.jobs.qsize(), 'capacity':self.jobs.maxsize,
                    'inflight':int(self._pending is not None), 'ping_interval_seconds':self.ping_interval}

    def _run(self):
        try: asyncio.run(self._serve())
        except BaseException:
            self._finish(self.ready, error=MemoryUnavailable('MCP 연결 시작 실패'))
        finally:
            with self.state_lock:
                self.closed = True; self.healthy = False
                self._stats['state'] = 'closed'
                if self._pending: self._finish(self._pending[-1], error=MemoryUnavailable('MCP 연결 종료'))
                self._drain_locked('MCP 연결이 종료되었습니다')

    async def _pause(self, seconds):
        until = time.monotonic() + seconds
        while not self.closed and time.monotonic() < until:
            await asyncio.sleep(min(.05, max(0, until-time.monotonic())))

    async def _serve(self):
        self._loop = asyncio.get_running_loop()
        self._wakeup = asyncio.Event()
        consecutive = 0
        outage_started = None
        while not self.closed:
            if consecutive:
                self._status(state='reconnecting')
                await self._pause(min(2., self.backoff * 2**(consecutive-1)))
                if self.closed: return
            if self.ready.done():
                with self.state_lock: self._stats['reconnect_attempts'] += 1
            self.secret = secrets.token_urlsafe(32)  # Old capabilities cannot cross a new process.
            params = StdioServerParameters(command=sys.executable, args=['-m','game.mcp_memory_server'],
                cwd=str(Path(__file__).resolve().parents[1]),
                env={'GAME_MEMORY_SECRET':self.secret, 'GAME_MEMORY_TOKEN_CACHE':os.environ.get('GAME_MEMORY_TOKEN_CACHE','false')})
            self._session_used = False
            try:
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=10)) as session:
                        async with asyncio.timeout(10):
                            await session.initialize()
                            tools = await session.list_tools()
                            if not {'register_context','search_memory','release_context'} <= {t.name for t in tools.tools}:
                                raise MemoryUnavailable('MCP 도구 누락')
                            rules = await session.read_resource('game://rules/evidence')
                        self.rules = '\n'.join(c.text for c in rules.contents if hasattr(c,'text'))
                        with self.state_lock:
                            if self.closed: return
                            self.healthy = True; self._stats['state'] = 'ready'
                            self._stats['last_success_at'] = time.time()
                            if outage_started is not None:
                                self._stats['recoveries'] += 1
                                self._stats['last_recovery_seconds'] = round(time.monotonic()-outage_started, 4)
                        outage_started = None
                        self._finish(self.ready, result=True)
                        await self._connected(session)
                        return
            except Exception:
                if self.closed: return
                with self.state_lock:
                    self.healthy = False
                    self._stats['failures'] += 1
                    self._stats['last_failure_at'] = time.time()
                    self._stats['state'] = 'reconnecting'
                if not self.ready.done(): raise  # Startup failure remains explicit.
                if outage_started is None: outage_started = time.monotonic()
                if self._session_used: consecutive = 0
                consecutive += 1
                # Only retry the read-only search once, with a newly registered scoped context.
                if self._pending:
                    if self._pending_retries >= 1 or self._pending[-1].done():
                        self._finish(self._pending[-1], error=MemoryUnavailable('MCP 복구 후 검색도 실패했습니다. 잠시 후 재개하세요.'))
                        self._pending = None
                    else: self._pending_retries += 1
                if consecutive > self.reconnect_limit:
                    with self.state_lock:
                        self._stats['state'] = 'cooldown'
                        if self._pending:
                            self._finish(self._pending[-1], error=MemoryUnavailable('MCP 재연결 한도 초과'))
                            self._pending = None
                        self._drain_locked('MCP 복구 대기 중입니다. 잠시 후 재개하세요.')
                    await self._pause(self.cooldown)
                    consecutive = 0

    async def _call(self, session, name, arguments, deadline):
        timeout = min(10., deadline-time.monotonic())
        if timeout <= 0: raise TimeoutError('MCP request expired')
        result = await asyncio.wait_for(session.call_tool(name, arguments), timeout)
        if result.isError: raise MemoryToolError('MCP 기억 도구 실행 실패')
        return result.structuredContent or json.loads(next(c.text for c in result.content if c.type=='text'))

    async def _connected(self, session):
        last_ping = time.monotonic()
        while not self.closed:
            if self._pending is None:
                self._wakeup.clear()
                try:
                    job = self.jobs.get_nowait()
                    if job is None: return
                    with self.state_lock:
                        if self.closed:
                            self._finish(job[-1], error=MemoryUnavailable('MCP 종료'))
                            return
                        self._pending = job; self._pending_retries = 0
                except queue.Empty:
                    if time.monotonic()-last_ping >= self.ping_interval:
                        await asyncio.wait_for(session.send_ping(), 2.)
                        last_ping = time.monotonic(); self._session_used = True
                        self._status(last_success_at=time.time())
                    try:
                        await asyncio.wait_for(self._wakeup.wait(), max(.01, self.ping_interval-(time.monotonic()-last_ping)))
                    except TimeoutError: pass
                    continue
            view, query, expanded, deadline, future = self._pending
            if future.cancelled() or time.monotonic() >= deadline:
                self._finish(future, error=MemoryUnavailable('MCP 검색 시간 초과'))
                self._pending = None
                continue
            token = None
            try:
                registered = await self._call(session, 'register_context', {'view':view,'proof':signature(view,self.secret)}, deadline)
                token = registered['capability']
                result = await self._call(session, 'search_memory', {'capability':token,'query':query or '', 'expanded':expanded}, deadline)
                result['evidence_rules'] = self.rules
            except MemoryToolError:
                self._finish(future, error=MemoryUnavailable('MCP 기억 도구가 요청을 거절했습니다.'))
                with self.state_lock: self._stats['tool_errors'] += 1
            else:
                self._finish(future, result=result)
                self._session_used = True
                self._status(last_success_at=time.time())
            finally:
                if token:
                    # A broken release invalidates the session; process exit clears its capabilities.
                    await self._call(session, 'release_context', {'capability':token}, time.monotonic()+1.)
            self._pending = None

    @traceable(name='mcp_memory_search',run_type='retriever',process_inputs=lambda d:{'query':d.get('query'),'expanded':d.get('expanded',False),'actor':d.get('view',{}).get('self',{}).get('id')})
    def search(self, view, query=None, expanded=False):
        with self.state_lock:
            if self.closed: raise MemoryUnavailable('MCP 연결이 닫혔습니다')
        snapshot = scoped(view)
        timeout = remaining(35)
        future = Future()
        with self.state_lock:
            if self.closed: raise MemoryUnavailable('MCP 연결이 닫혔습니다')
            if getattr(self, '_stats', {}).get('state') == 'cooldown':
                raise MemoryUnavailable('MCP 복구 대기 중입니다. 잠시 후 재개하세요.')
            try: self.jobs.put_nowait((snapshot, query, expanded, time.monotonic()+timeout, future))
            except queue.Full: raise MemoryUnavailable('MCP 검색 대기열이 가득 찼습니다. 잠시 후 재개하세요.') from None
        self._wake()
        try: return future.result(timeout=timeout)
        except TimeoutError:
            future.cancel()
            raise MemoryUnavailable('MCP 검색 시간 초과') from None

    def _wake(self):
        loop = getattr(self, '_loop', None)
        if loop is not None and not loop.is_closed():
            try: loop.call_soon_threadsafe(self._wakeup.set)
            except RuntimeError: pass

    def close(self):
        with self.state_lock:
            if not self.closed:
                self.closed = True; self.healthy = False
                if hasattr(self, '_stats'): self._stats['state'] = 'closed'
                if getattr(self, '_pending', None): self._finish(self._pending[-1], error=MemoryUnavailable('MCP 종료로 검색 취소'))
                self._drain_locked('MCP 종료로 검색을 취소했습니다')
                self.jobs.put_nowait(None)
        self._wake()
        if threading.current_thread() != self.thread: self.thread.join(timeout=15)
