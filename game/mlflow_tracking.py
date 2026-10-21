"""Optional local MLflow REST sink. Bounded queue, short timeouts, numeric data only."""
import copy
import math
import queue
import threading
import time
from urllib.parse import urlsplit
import httpx
from .action_telemetry import ACTION_METRICS, DECISION_METRICS

METRICS = frozenset(ACTION_METRICS + DECISION_METRICS)


def local_uri(uri):
    p=urlsplit(uri)
    if p.scheme!='http' or p.hostname not in ('127.0.0.1','localhost','::1') or p.username or p.password or p.query or p.fragment or p.path not in ('','/'):
        raise ValueError('MLflow URI는 인증정보 없는 로컬 HTTP 주소여야 합니다')
    return uri.rstrip('/')


class TrackingClient:
    def __init__(self, uri):
        self.uri=local_uri(uri);self.http=httpx.Client(base_url=self.uri,timeout=1.5,trust_env=False,follow_redirects=False)

    def call(self, route, body=None, params=None):
        r=self.http.request('POST' if body is not None else 'GET','/api/2.0/mlflow/'+route,json=body,params=params)
        r.raise_for_status();return r.json()

    def experiment(self, name):
        try:return self.call('experiments/get-by-name',params={'experiment_name':name})['experiment']['experiment_id']
        except httpx.HTTPStatusError as error:
            if error.response.status_code!=404:raise
        try:return self.call('experiments/create',{'name':name})['experiment_id']
        except httpx.HTTPStatusError as error:
            if error.response.status_code!=400:raise
            return self.call('experiments/get-by-name',params={'experiment_name':name})['experiment']['experiment_id']

    def create_run(self, experiment_id, name, params=None):
        run=self.call('runs/create',{'experiment_id':experiment_id,'start_time':int(time.time()*1000),
                                  'tags':[{'key':'mlflow.runName','value':name}]})['run']['info']['run_id']
        if params:self.call('runs/log-batch',{'run_id':run,'params':[{'key':k,'value':str(v)} for k,v in params.items()]})
        return run

    def log(self, run, values, step):
        metrics=[{'key':k,'value':float(v),'timestamp':int(time.time()*1000),'step':step}
                 for k,v in values.items() if isinstance(v,(int,float)) and math.isfinite(v)]
        self.call('runs/log-batch',{'run_id':run,'metrics':metrics})

    def end(self, run):
        self.call('runs/update',{'run_id':run,'status':'FINISHED','end_time':int(time.time()*1000)})

    def health(self):self.http.get('/health').raise_for_status()

    def close(self):self.http.close()


class RuntimeExporter:
    def __init__(self, uri, max_pending=64, client_factory=TrackingClient, experiment_name="night-testimony-runtime"):
        self.experiment_name=experiment_name
        self.uri=local_uri(uri);self.factory=client_factory;self.pending=queue.Queue(maxsize=max_pending)
        self.stop=threading.Event();self.lock=threading.Lock()
        self.state={'state':'connecting','sent':0,'dropped':0,'failures':0,'uri':self.uri,'experiment_id':None,'run_id':None,'checked_at':None}
        self.worker=threading.Thread(target=self._work,name='mlflow-metrics',daemon=True);self.worker.start()

    def submit(self, values):
        # Caller supplies scalar metrics. Copy to detach from game state.
        sample={k:float(v) for k,v in values.items() if k in METRICS and isinstance(v,(int,float)) and math.isfinite(v)}
        with self.lock:
            if self.stop.is_set():return
            try:self.pending.put_nowait(sample)
            except queue.Full:self.state['dropped']+=1

    def snapshot(self):
        with self.lock:return {**copy.deepcopy(self.state),'pending':self.pending.qsize(),'capacity':self.pending.maxsize}

    def _work(self):
        client=None;run=None;step=0;experiment=None;probed=0.
        try:
            while not self.stop.is_set():
                try:
                    if client is None:client=self.factory(self.uri)
                    if time.monotonic()-probed>=10:
                        client.health();probed=time.monotonic()
                        with self.lock:self.state.update(state='ready',checked_at=time.time())
                    if experiment is None:
                        experiment=client.experiment(self.experiment_name)
                        with self.lock:self.state.update(state='ready',experiment_id=experiment)
                    try:sample=self.pending.get(timeout=.5)
                    except queue.Empty:continue
                    try:
                        if run is None:run=client.create_run(experiment,'server-'+time.strftime('%Y%m%d-%H%M%S'),{'scope':'process lifetime; numeric only', 'metrics_schema':'decision-and-action-v2'})
                        client.log(run,sample,step);step+=1
                        with self.lock:self.state.update(state='ready',run_id=run,sent=self.state['sent']+1)
                    except Exception:
                        with self.lock:self.state.update(state='unavailable',failures=self.state['failures']+1,dropped=self.state['dropped']+1)
                        self.stop.wait(3)
                    finally:self.pending.task_done()
                except Exception:
                    with self.lock:self.state.update(state='unavailable',failures=self.state['failures']+1)
                    self.stop.wait(3)
        finally:
            if run:
                try:client.end(run)
                except Exception:pass
            if client:client.close()
            with self.lock:
                self.state['dropped']+=self.pending.qsize();self.state['state']='closed'

    def close(self):
        self.stop.set();self.worker.join(timeout=10)
