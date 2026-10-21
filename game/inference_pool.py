"""Bounded cross-room concurrency; callers retain per-room ordering."""
import asyncio
from contextlib import asynccontextmanager
from .providers import ProviderError


class InferencePool:
    def __init__(self, concurrency=2, max_waiters=16, wait_timeout=30):
        if not 1 <= concurrency <= 8 or max_waiters < 1 or wait_timeout <= 0:
            raise ValueError('AI 실행 설정 범위를 확인하세요')
        self.semaphore = asyncio.Semaphore(concurrency)
        self.max_waiters = max_waiters
        self.wait_timeout = wait_timeout
        self.waiters = 0
        self.active = 0

    @asynccontextmanager
    async def slot(self):
        if self.waiters >= self.max_waiters:
            raise ProviderError('AI 대기열이 가득 찼습니다. 잠시 후 재개하세요.')
        self.waiters += 1
        try:
            try:
                await asyncio.wait_for(self.semaphore.acquire(), self.wait_timeout)
            except asyncio.TimeoutError:
                raise ProviderError('AI 실행 대기 시간이 초과되었습니다. 잠시 후 재개하세요.') from None
        finally:
            self.waiters -= 1
        self.active += 1
        try:
            yield
        finally:
            self.active -= 1
            self.semaphore.release()


async def run_blocking(function, *args, **kwargs):
    # Cancelling to_thread does not stop its thread. Drain it before releasing a slot
    # or closing shared HTTP/MCP resources, and do not commit its result afterwards.
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue  # A second cancellation must not cancel the worker wrapper.
            except Exception:
                break
        if not task.cancelled():
            task.exception()  # Retrieve any worker failure while preserving cancellation.
        raise


async def create_blocking_resource(factory):
    """Close a newly-created resource if startup is cancelled before handoff."""
    created = []
    def build():
        resource = factory()
        created.append(resource)
        return resource
    try:
        return await run_blocking(build)
    except asyncio.CancelledError:
        if created:
            try:
                await run_blocking(created[0].close)
            except asyncio.CancelledError:
                pass  # Cleanup has drained despite repeated cancellation.
        raise
