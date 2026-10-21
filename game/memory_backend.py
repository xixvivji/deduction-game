"""Permission-filtered memory contract shared by local baseline and MCP service."""
import copy
import hashlib
import hmac
import json
from langsmith import traceable
from .memory import retrieve

RULES = '참가자는 자신의 관측만 사용한다. 발언은 주장이고 조사·검시·공격 목격은 서버 사실이다. 위치 목격만으로 공격이나 진영을 확정할 수 없다. 근거 부족 시 추측으로 표시하거나 기권한다. 늑대의 기만은 게임 규칙상 허용된다.'


def scoped(view):
    result=copy.deepcopy(view);pid=result['self']['id']
    result['events']=[e for e in result['events'] if e.get('recipient') is None or e.get('recipient')==pid or isinstance(e.get('recipient'),list) and pid in e['recipient']]
    result.pop('roles',None)
    result['players']=[{k:p[k] for k in ('id','alive','controller') if k in p} for p in result['players']]
    return result


def signature(view,secret):
    return hmac.new(secret.encode(),json.dumps(view,sort_keys=True,ensure_ascii=False).encode(),hashlib.sha256).hexdigest()


class LocalMemory:
    name='local'
    @traceable(name='local_memory_search',run_type='retriever')
    def search(self,view,query=None,expanded=False):
        return retrieve(scoped(view),query,top_k=16 if expanded else 8)
    def close(self):pass
