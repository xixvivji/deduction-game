"""Per-endpoint transient-failure breaker shared by server HTTP requests."""
import threading
import time
from .providers import ProviderError, TransientProviderError


class CircuitTransport:
    def __init__(self, transport, threshold=3, cooldown=30, clock=time.monotonic):
        self.transport=transport;self.threshold=threshold;self.cooldown=cooldown;self.clock=clock
        self.states={};self.lock=threading.Lock()

    def __call__(self,url,body,headers,timeout):
        with self.lock:
            state=self.states.setdefault(url,{'failures':0,'until':0,'probe':False,'generation':0})
            if state['until']:
                if self.clock()<state['until'] or state['probe']:
                    raise ProviderError('모델 API 반복 장애로 호출을 잠시 중단했습니다. 30초 후 재개하세요.')
                state['probe']=True
            generation=state['generation']
        try:
            result=self.transport(url,body,headers,timeout)
        except TransientProviderError:
            with self.lock:
                if generation==state['generation']:
                    state['failures']+=1
                    if state['probe'] or state['failures']>=self.threshold:
                        state.update(until=self.clock()+self.cooldown,probe=False,generation=generation+1)
            raise
        except BaseException:
            # Auth/schema errors do not count as transient outages; release a probe.
            with self.lock:
                if generation==state['generation']:
                    state.update(failures=0,until=0,probe=False)
            raise
        with self.lock:
            if generation==state['generation']:state.update(failures=0,until=0,probe=False)
        return result

    def close(self):self.transport.close()
