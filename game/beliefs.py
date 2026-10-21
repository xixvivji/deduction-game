"""Private, evidence-linked beliefs. Facts are derived by code; suspicions are model opinions."""
import copy
from .memory_backend import scoped


def _facts(event):
    d=event['data'];kind=event['type']
    if kind=='조사' and d.get('team') in ('wolf','village'):
        return d.get('target'), 'confirmed_'+d['team']
    if kind=='검시' and d.get('role') in ('wolf','citizen','seer','doctor','tracker','coroner'):
        return d.get('target'), 'confirmed_wolf' if d['role']=='wolf' else 'confirmed_village'
    if kind=='공격 목격':
        return d.get('actor'), 'confirmed_wolf'
    return None,None


def update(view, previous=None):
    view=scoped(view)
    owner=view['self']['id'];room=view.get('room_id')
    previous=previous or {}
    if previous.get('owner')!=owner or previous.get('room_id')!=room:
        previous={}
    events={e['sequence']:e for e in view['events']}
    entries={};changes=[]
    for player in view['players']:
        target=player['id']
        if target==owner:continue
        old=previous.get('entries',{}).get(target,{})
        facts=[(e,_facts(e)[1]) for e in events.values() if _facts(e)[0]==target]
        states={status for _,status in facts}
        state='conflicted' if len(states)>1 else next(iter(states)) if states else 'unknown'
        ids=[e['sequence'] for e,_ in facts]
        if not facts and old.get('status')=='suspected':
            ids=[i for i in old.get('evidence_ids',[]) if i in events and target in [events[i]['data'].get(k) for k in ('actor','target','player')]]
            if ids:state='suspected'
        reason={'unknown':'진영을 판단할 근거가 아직 없습니다.',
                'suspected':'관련 기록에 기반한 이전 의심이며 확정 사실은 아닙니다.',
                'confirmed_wolf':'본인에게 허용된 조사·검시·공격 목격이 늑대 진영을 뒷받침합니다.',
                'confirmed_village':'본인에게 허용된 조사·검시가 시민 진영을 뒷받침합니다.',
                'conflicted':'허용된 확정 기록이 서로 충돌합니다. 진영 확정을 보류합니다.'}[state]
        changed=old.get('status','unknown')!=state
        entries[target]={'status':state,'alive':player['alive'],'evidence_ids':sorted(set(ids)),
                         'reason':reason,'updated_day':view['day'] if changed or not old or old.get('evidence_ids')!=sorted(set(ids)) else old.get('updated_day',view['day'])}
        if changed:
            changes.append({'target':target,'from':old.get('status','unknown'),'to':state,
                            'day':view['day'],'evidence_ids':sorted(set(ids)),'reason':reason})
    return {'owner':owner,'room_id':room,'entries':entries,'changes':changes,
            'history':(copy.deepcopy(previous.get('history',[]))+changes)[-20:]}


def record_suspicion(view, ledger, audit, fallback):
    """Never turn a wolf's public deception into its private belief."""
    if fallback or view['self']['role']=='wolf' or audit.get('semantic_support')!='grounded_suspicion':return ledger
    claim=audit.get('claim',{});target=claim.get('claim_subject');entry=ledger['entries'].get(target)
    if not entry or entry['status'] not in ('unknown','suspected'):return ledger
    visible={e['sequence']:e for e in scoped(view)['events']}
    ids=[i for i in audit.get('evidence_ids',[]) if i in visible and target in [visible[i]['data'].get(k) for k in ('actor','target','player')]]
    if not ids:return ledger
    ledger=copy.deepcopy(ledger);old=ledger['entries'][target]
    ledger['entries'][target]={**old,'status':'suspected','evidence_ids':ids,
        'reason':'AI가 관련 기록을 근거로 의심을 선택했습니다. 진영을 확정한 것은 아닙니다.','updated_day':view['day']}
    if old['status']!='suspected':
        change={'target':target,'from':old['status'],'to':'suspected','day':view['day'],
                'evidence_ids':ids,'reason':ledger['entries'][target]['reason']}
        ledger['changes'].append(change);ledger['history']=(ledger['history']+[change])[-20:]
    return ledger


def attach(selected, ledger, original):
    selected['belief_memory']=copy.deepcopy(ledger)
    ids={i for item in ledger['entries'].values() for i in item['evidence_ids']}
    events={e['sequence']:e for e in selected['events']}
    for e in scoped(original)['events']:
        if e['sequence'] in ids:events[e['sequence']]=e
    selected['events']=[events[i] for i in sorted(events)]
    if 'memory' in selected:selected['memory']['selected_events']=len(events)


def check(view, ledger, claim, payload):
    if view['self']['role']=='wolf':return None
    kind=claim.get('claim_type');target=claim.get('claim_subject')
    entry=ledger['entries'].get(target,{})
    status=entry.get('status')
    invalid=(status=='confirmed_village' and kind in ('suspect_wolf','confirmed_wolf','witnessed_attack')) or (status=='confirmed_wolf' and kind=='confirmed_village') or (status=='conflicted' and kind in ('confirmed_wolf','confirmed_village','witnessed_attack'))
    if view['phase']=='vote' and ledger['entries'].get(payload.get('target'),{}).get('status')=='confirmed_village':invalid=True
    if invalid:return {'ok':False,'status':'belief_conflict','reason':'개인 판단 기록의 확정 근거와 충돌합니다. 새 조사·검시 결과를 반영하고 모순된 확정을 철회하세요.'}
    return None


def retraction(ledger, claim):
    target=claim.get('claim_subject')
    if claim.get('claim_type')=='confirmed_village' and any(c['target']==target and c['from']=='suspected' and c['to']=='confirmed_village' for c in ledger['changes']):
        return '새 확인 근거에 따라 이전 의심을 철회합니다. '
    return ''


def instructions(view):
    if not view.get('belief_memory'):return ''
    return '''\nbelief_memory는 본인만 볼 수 있는 개인 판단 기록이다. suspected는 의심, confirmed_*는 허용된 시스템 근거, conflicted는 확정 기록 충돌이다. unknown은 정보 없음이다. 사망자의 기록은 과거 회고이며 현재 투표 대상으로 사용하지 않는다.
새 근거가 이전 의심을 뒤집으면 그 변화를 반영하라. 공개 발언에서 어떤 개인 정보를 공개할지는 역할 전략에 따라 결정하되 기록에 없는 사실을 만들지 마라. 이 기록은 자유 발언이나 다른 참가자의 주장이 진실이라는 보장이 아니다. 늑대는 개인 판단과 공개 기만을 구분하며 개인 판단 기록이나 동료 정보를 그대로 낭독하지 않는다.'''
