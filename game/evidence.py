"""Deterministic verifier for typed game facts, not an unrestricted prose truth detector."""
from langsmith import traceable
from .claim_guard import inspect_opinion
from .suspicion import DEFAULT_SUSPICION_POLICY, VILLAGE_ROLES

CLAIMS = ['none','suspect_wolf','confirmed_wolf','confirmed_village','witnessed_attack','visited']


def claim_schema(view):
    return {'claim_type':{'enum':CLAIMS},
            'claim_subject':{'enum':[None]+[p['id'] for p in view['players']]},
            'claim_value':{'type':'string','minLength':0,'maxLength':80}}


@traceable(name='evidence_verifier', run_type='chain')
def verify(view, claim, ids, payload, suspicion_policy=DEFAULT_SUSPICION_POLICY):
    kind,subject,value=(claim.get(k) for k in ('claim_type','claim_subject','claim_value'))
    events={e['sequence']:e for e in view['events']}
    evidence=[events[i] for i in ids if i in events]
    if len(evidence)!=len(ids):return {'ok':False,'status':'invalid_citation','reason':'전달받은 기록만 인용하세요.'}
    if kind=='none':
        ok=not subject and not value
        if view['phase']=='vote' and payload.get('target') and view['self']['role']!='wolf':ok=False
        guard = inspect_opinion(view, payload.get('text',''), ids) if ok and view['self']['role']!='wolf' else None
        if guard and not guard['ok']:
            return {'ok':False, 'status':'untyped_fact','opinion_guard':guard,
                    'reason':'의견 안에 사실 단정이 있습니다. 해당 사실을 claim_type과 evidence_ids로 구조화하거나 근거 없는 단정을 빼고 확인 질문으로 고치세요.'}
        status = 'game_deception_allowed' if ok and view['self']['role'] == 'wolf' else 'opinion'
        return {'ok':ok,'status':status if ok else 'insufficient','opinion_guard':guard,'reason':'' if ok else '투표 대상을 뒷받침하는 관련 기록을 인용하거나 기권하세요.'}
    if not subject:return {'ok':False,'status':'insufficient','reason':'주장의 대상이 필요합니다.'}
    if view['self']['role']=='wolf':
        return {'ok':True,'status':'game_deception_allowed','reason':'늑대의 게임 내 주장은 사실로 인증하지 않습니다.'}
    if kind=='suspect_wolf':
        supported=bool(suspicion_policy.evidence(evidence, subject, view['day']))
    else:
        def supports(e):
            d=e['data'];t=e['type']
            if kind=='confirmed_wolf':return (t=='조사' and d.get('target')==subject and d.get('team')=='wolf') or (t=='검시' and d.get('target')==subject and d.get('role')=='wolf') or (t=='공격 목격' and d.get('actor')==subject)
            if kind=='confirmed_village':return (t=='조사' and d.get('target')==subject and d.get('team')=='village') or (t=='검시' and d.get('target')==subject and d.get('role') in VILLAGE_ROLES)
            if kind=='witnessed_attack':return t=='공격 목격' and d.get('actor')==subject and d.get('target')==value
            if kind=='visited':return ((t in ('목격','탐색 목격') and d.get('player')==subject) or (t=='추적' and d.get('target')==subject)) and d.get('location')==value
            return False
        supported=any(supports(e) for e in evidence)
    if view['phase']=='vote' and payload.get('target') and (payload['target']!=subject or kind not in ('confirmed_wolf','suspect_wolf','witnessed_attack')):supported=False
    return {'ok':supported,'status':('supported_fact' if kind!='suspect_wolf' else 'grounded_suspicion') if supported else 'insufficient',
            'reason':'' if supported else '인용 기록이 주장을 뒷받침하지 않습니다. 위치 목격만으로 늑대나 공격을 확정하지 마세요.'}


def statement(claim, evidence=()):
    subject,value=claim['claim_subject'],claim['claim_value']
    # Cite an actual matching source, not a free-form time supplied by the model.
    matching = [e for e in evidence if
                (claim['claim_type'] == 'visited' and e['data'].get('location') == value and
                 ((e['type'] in ('목격','탐색 목격') and e['data'].get('player') == subject) or
                  (e['type'] == '추적' and e['data'].get('target') == subject))) or
                (claim['claim_type'] == 'witnessed_attack' and e['type'] == '공격 목격' and
                 e['data'].get('actor') == subject and e['data'].get('target') == value)]
    source = matching[-1] if matching else None
    when = '인용 기록의 해당 시점'
    if source:
        data = source['data'];parts = []
        if type(data.get('day')) is int:parts.append(f"{data['day']}일 차")
        if type(data.get('second')) in (int,float):parts.append(f"탐색 {data['second']}초")
        when = (' '.join(parts) or when) + f" (기록 #{source['sequence']})"
    return {'confirmed_wolf':f'{subject}는 확인된 기록상 늑대 진영입니다.',
            'confirmed_village':f'{subject}는 확인된 기록상 시민 진영입니다.',
            'witnessed_attack':f'{when}에 {subject}가 {value}를 공격한 목격 기록이 있습니다.',
            'visited':f'{when}에 {subject}는 {value}에 있었습니다. 현재 위치나 전체 동선을 의미하지 않습니다.',
            'suspect_wolf':f'관련 기록을 근거로 {subject}를 의심하지만 늑대라고 확정할 수는 없습니다.'}[claim['claim_type']]
