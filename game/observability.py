"""Process-local, bounded numeric telemetry. Never retain prompts, identities or evidence."""
from collections import Counter, deque
import copy
import math
import resource
import sys
import threading
import time
from uuid import uuid4
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables.config import set_config_context

NODES = ('memory', 'generate', 'validate_evidence', 'research', 'repair', 'fallback')
EDGES = [('memory','generate'),('generate','validate_evidence'),('generate','generate'),
         ('generate','fallback'),('validate_evidence','research'),('research','generate'),
         ('validate_evidence','repair'),('repair','generate'),('validate_evidence','fallback')]


def number(value):
    return value if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0 else 0


class NodeObserver(BaseCallbackHandler):
    """Only node names, elapsed time and status cross the telemetry boundary."""
    def __init__(self, monitor, trace_id):
        self.monitor=monitor; self.trace_id=trace_id; self.runs={}

    def on_chain_start(self, serialized, inputs, *, run_id, metadata=None, **kwargs):
        node=(metadata or {}).get('langgraph_node')
        if node in NODES and kwargs.get('name')==node:
            self.runs[run_id]=(node,time.perf_counter())
            self.monitor.stage(self.trace_id,node,'running')

    def on_chain_end(self, outputs, *, run_id, **kwargs):
        self._end(run_id,False,outputs)

    def on_chain_error(self, error, *, run_id, **kwargs):
        self._end(run_id,True)

    def _end(self, run_id, failed, outputs=None):
        entry=self.runs.pop(run_id,None)
        if entry:
            node,started=entry
            rejected = isinstance(outputs,dict) and (outputs.get('verification',{}).get('ok') is False or bool(outputs.get('error')))
            self.monitor.stage(self.trace_id,node,'error' if failed else 'rejected' if rejected else 'complete',time.perf_counter()-started)


class Monitor:
    def __init__(self, exporter=None, history_limit=200):
        self.source="현재 게임 서버 프로세스"
        self.started=time.time(); self.clock=time.monotonic()
        self.lock=threading.Lock(); self.active={}; self.recent=deque(maxlen=history_limit)
        self.totals=Counter(); self.node_totals=Counter(); self.exporter=exporter

    def stage(self, trace_id, node, status, seconds=None):
        with self.lock:
            trace=self.active.get(trace_id)
            if trace is None:return
            trace['current_node']=node
            if status=='running':
                if len(trace['stages'])<40:trace['stages'].append({'node':node,'status':status,'seconds':None})
            else:
                for stage in reversed(trace['stages']):
                    if stage['node']==node and stage['status']=='running':
                        stage.update(status=status,seconds=round(seconds,4));break
                self.node_totals[node]+=1

    def run(self, backend, client, function, *args, queue_wait=0., **kwargs):
        trace_id=uuid4().hex[:12]; started=time.perf_counter()
        backend=backend if backend in ('openai','gemini','ollama','demo') else 'other'
        before=len(getattr(client,'metrics',[]))
        trace={'id':trace_id,'backend':backend,'started_at':time.time(),'status':'running','current_node':'starting','stages':[]}
        with self.lock:self.active[trace_id]=trace
        outcome=None; failed=False
        try:
            # Use the documented config context, without changing the frozen decision graph.
            with set_config_context({'callbacks':[NodeObserver(self,trace_id)]}) as ctx:
                outcome=ctx.run(function,*args,**kwargs)
            return outcome
        except BaseException:
            failed=True
            raise
        finally:
            metrics=getattr(client,'metrics',[])[before:]
            self._finish(trace_id,started,outcome,metrics,failed,queue_wait)

    def _finish(self, trace_id, started, outcome, metrics, failed, queue_wait):
        outcome=outcome or {}; audit=outcome.get('audit',{}); runtime=audit.get('runtime',{})
        summary={'queue_wait_seconds':number(queue_wait),'seconds':round(time.perf_counter()-started,4),
                 'attempts':int(number(outcome.get('attempts'))),
                 'retries':max(0,int(number(outcome.get('attempts')))-1),
                 'fallback':bool(outcome.get('fallback')), 'failed':failed,
                 'api_calls':len(metrics), 'api_errors':sum(m.get('success') is False for m in metrics),
                 'input_tokens':sum(number(m.get('input_tokens')) for m in metrics),
                 'output_tokens':sum(number(m.get('output_tokens')) for m in metrics),
                 'usage_known_calls':sum(m.get('input_tokens') is not None and m.get('output_tokens') is not None for m in metrics),
                 'selected_events':number(runtime.get('selected_events')),
                 'visible_events':number(runtime.get('visible_events')),
                 'forced':runtime.get('route')=='forced_action'}
        with self.lock:
            trace=self.active.pop(trace_id)
            trace.update(summary,status='error' if failed else 'fallback' if summary['fallback'] else 'complete',current_node=None)
            self.recent.appendleft(trace); self.totals['decisions']+=1
            for key in ('api_calls','api_errors','input_tokens','output_tokens','usage_known_calls','retries','fallback','failed','forced'):
                self.totals[key]+=summary[key]
        if self.exporter:
            try:self.exporter.submit({k:float(summary[k]) for k in ('seconds','api_calls','api_errors','input_tokens','output_tokens','retries','fallback','failed','forced')})
            except Exception:pass  # Telemetry must not alter a completed game decision.

    def snapshot(self):
        with self.lock:
            recent=copy.deepcopy(list(self.recent));active=copy.deepcopy(list(self.active.values()))
            totals=dict(self.totals);nodes=dict(self.node_totals)
        times=sorted(t['seconds'] for t in recent)
        usage=resource.getrusage(resource.RUSAGE_SELF)
        return {'source':self.source,'started_at':self.started,'uptime_seconds':round(time.monotonic()-self.clock),
                'peak_rss_bytes':int(usage.ru_maxrss*(1 if sys.platform=='darwin' else 1024)),
                'history_limit':self.recent.maxlen,'retained_decisions':len(recent),
                'totals':totals,'nodes':nodes,'active':active,'recent':recent[:30],
                'recent_p50_seconds':times[len(times)//2] if times else None,
                'recent_p95_seconds':times[max(0,math.ceil(len(times)*.95)-1)] if times else None,
                'graph':{'nodes':NODES,'edges':EDGES},
                'mlflow':self.exporter.snapshot() if self.exporter else {'state':'disabled'}}
