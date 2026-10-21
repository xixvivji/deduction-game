"""Deterministic verifier for typed game facts, not an unrestricted prose truth detector."""
from langsmith import traceable

CLAIMS = ['none','suspect_wolf','confirmed_wolf','confirmed_village','witnessed_attack','visited']


def claim_schema(view):
    return {'claim_type':{'enum':CLAIMS},
            'claim_subject':{'enum':[None]+[p['id'] for p in view['players']]},
            'claim_value':{'type':'string','minLength':0,'maxLength':80}}


@traceable(name='evidence_verifier', run_type='chain')
def verify(view, claim, ids, payload):
    kind,subject,value=(claim.get(k) for k in ('claim_type','claim_subject','claim_value'))
    events={e['sequence']:e for e in view['events']}
    evidence=[events[i] for i in ids if i in events]
    if len(evidence)!=len(ids):return {'ok':False,'status':'invalid_citation','reason':'전달받은 기록만 인용하세요.'}
    if kind=='none':
        ok=not subject and not value
        if view['phase']=='vote' and payload.get('target') and view['self']['role']!='wolf':ok=False
        return {'ok':ok,'status':'opinion' if ok else 'insufficient','reason':'투표 대상을 뒷받침하는 관련 기록을 인용하거나 기권하세요.'}
    if not subject:return {'ok':False,'status':'insufficient','reason':'주장의 대상이 필요합니다.'}
    if view['self']['role']=='wolf':
        return {'ok':True,'status':'game_deception_allowed','reason':'늑대의 게임 내 주장은 사실로 인증하지 않습니다.'}
    if kind=='suspect_wolf':
        supported=any(subject in [e['data'].get(k) for k in ('actor','target','player')] for e in evidence)
    else:
        def supports(e):
            d=e['data'];t=e['type']
            if kind=='confirmed_wolf':return (t=='조사' and d.get('target')==subject and d.get('team')=='wolf') or (t=='검시' and d.get('target')==subject and d.get('role')=='wolf') or (t=='공격 목격' and d.get('actor')==subject)
            if kind=='confirmed_village':return (t=='조사' and d.get('target')==subject and d.get('team')=='village') or (t=='검시' and d.get('target')==subject and d.get('role')!='wolf')
            if kind=='witnessed_attack':return t=='공격 목격' and d.get('actor')==subject and d.get('target')==value
            if kind=='visited':return ((t in ('목격','탐색 목격') and d.get('player')==subject) or (t=='추적' and d.get('target')==subject)) and d.get('location')==value
            return False
        supported=any(supports(e) for e in evidence)
    if view['phase']=='vote' and payload.get('target') and (payload['target']!=subject or kind not in ('confirmed_wolf','suspect_wolf','witnessed_attack')):supported=False
    return {'ok':supported,'status':('supported_fact' if kind!='suspect_wolf' else 'grounded_suspicion') if supported else 'insufficient',
            'reason':'' if supported else '인용 기록이 주장을 뒷받침하지 않습니다. 위치 목격만으로 늑대나 공격을 확정하지 마세요.'}


def statement(claim):
    subject,value=claim['claim_subject'],claim['claim_value']
    return {'confirmed_wolf':f'{subject}는 확인된 기록상 늑대 진영입니다.',
            'confirmed_village':f'{subject}는 확인된 기록상 시민 진영입니다.',
            'witnessed_attack':f'목격 기록에서 {subject}가 {value}를 공격했습니다.',
            'visited':f'인용 기록의 해당 시점에 {subject}는 {value}에 있었습니다. 전체 동선을 의미하지 않습니다.',
            'suspect_wolf':f'관련 기록을 근거로 {subject}를 의심하지만 늑대라고 확정할 수는 없습니다.'}[claim['claim_type']]
