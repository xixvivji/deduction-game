"""Bounded Korean discussion guard; heuristics, not a general semantic truth judge."""
import re
from difflib import SequenceMatcher

SPEECH_TYPES = {'발언', '답변'}
DEFER = re.compile(r'근거.{0,12}부족|정보.{0,12}부족|추가.{0,10}(?:정보|조사)|신중|기다리|판단.{0,6}보류')


def context(view):
    """Only the already scoped participant snapshot is accepted by the caller."""
    me = view['self']['id']
    speeches = [e for e in view['events'] if e['type'] in SPEECH_TYPES and e.get('recipient') is None]
    own = [e for e in speeches if e['data'].get('actor') == me][-3:]
    return {'alive': [p['id'] for p in view['players'] if p['alive']],
            'dead': [p['id'] for p in view['players'] if not p['alive']],
            'own_recent': own, 'public_recent': speeches[-3:], 'day': view['day']}



def attach_context(selected, original):
    ctx = context(original)
    selected['discussion_context'] = ctx
    # A record shown in the auxiliary context must also be a valid citation.
    events = {e['sequence']:e for e in selected['events']}
    for event in ctx['own_recent'] + ctx['public_recent']:
        events[event['sequence']] = event
    selected['events'] = [events[key] for key in sorted(events)]
    if 'memory' in selected:
        selected['memory']['selected_events'] = len(events)
    return selected


def instructions(view):
    if not view.get('discussion_context'):
        return ''
    return '''\n토론 품질 규칙: discussion_context의 alive/dead는 현재 생존 상태다. 사망자는 현재 투표·추방 대상으로 제안하지 않는다. 사망자의 과거 행동이나 검시 결과 언급은 가능하다.
목격은 기록에 적힌 과거 시점의 정보다. 위치를 말할 때 해당 일차/기록 당시라고 표현하고 현재 위치로 단정하지 않는다.
own_recent와 public_recent의 문장을 반복하거나 단순 동의하지 않는다. 새 정보가 없으면 살아 있는 특정 참가자에게 확인하고 싶은 구체적인 질문 하나를 공개 발언으로 제시하거나, 기존 발언 하나의 근거를 비교한다. 기록에 없는 사건을 만들지 않는다. 질문 문장은 자동으로 상대의 차례를 추가하지 않는다.
지목 질문이 있으면 그 질문에 먼저 직접 답하고, 같은 사실 재확인이 필요할 때는 재확인임을 밝혀라. 의견 접두사는 서버가 붙이므로 직접 쓰지 않는다.'''


def normalize(text):
    text = re.sub(r'^(?:(?:의견|주장)\s*[:：]\s*)+', '', text.strip())
    return re.sub(r'[^가-힣a-z0-9]', '', text.lower())


def check(view, text, semantic_status):
    ctx = context(view)
    issues = []
    for sentence in re.split(r'[.!?。\n]', text):
        if not sentence.strip():
            continue
        historical = re.search(r'어제|지난|이전|과거|당시|기록.{0,5}시점|\d+일\s*차', sentence)
        reported = re.search(r'제안했|말했|주장했|제안이 있었|의견이 있었', sentence) and not re.search(r'(?:동의|찬성)(?:합니다|한다|해요|하겠습니다)', sentence)
        negative = re.search(r'투표.{0,15}(?:않|못|불가|제외|없)|(?:아니|말자|말아|대신|제외)', sentence)
        for pid in ctx['dead']:
            direct = re.search(re.escape(pid)+r'(?!\d).{0,30}(?:투표|추방)', sentence)
            if direct and not historical and not negative and not reported:
                issues.append('dead_vote_target')
        if not historical and not re.search(r'알 수 없|단정.{0,10}(?:못|없)|있나요|있습니까', sentence) and re.search(r'현재|지금', sentence) and re.search(r'p\d+.{0,30}(?:광장|창고|숲길|진료소|시장|종탑|여관|부두).{0,15}(?:있|보이)', sentence):
            issues.append('unqualified_current_location')
    # A direct question can legitimately require repeating a previous answer.
    if not view.get('question'):
        current = normalize(text)
        previous = [e['data'].get('text', '') for e in ctx['own_recent']]
        if len(current) >= 15 and any(SequenceMatcher(None,current,normalize(old)).ratio() >= .88 for old in previous):
            issues.append('near_duplicate')
        if semantic_status == 'opinion' and len(DEFER.findall(text)) >= 2 and any(len(DEFER.findall(old)) >= 2 for old in previous) and '?' not in text:
            issues.append('repeated_deferral')
    return {'ok':not issues, 'issues':sorted(set(issues))}


def feedback(result):
    messages = {'dead_vote_target':'사망자를 현재 투표·추방 대상으로 제안하지 마세요.',
                'unqualified_current_location':'과거 목격을 현재 위치로 단정하지 말고 기록 당시의 시점을 명시하세요.',
                'near_duplicate':'이전 내 발언과 거의 같습니다. 새 근거나 살아 있는 대상에 대한 구체적 확인 질문을 제시하세요.',
                'repeated_deferral':'판단 보류를 반복하지 말고, 살아 있는 참가자에게 확인할 구체적 질문을 하나 제시하세요.'}
    return ' '.join(messages[i] for i in result['issues'])


def safe_speech(view):
    if view.get('question'):
        return '의견: 질문에 답할 수 있는 검증된 설명을 이번 차례에 만들지 못했습니다. 이 답변을 새로운 사실로 해석하지 말아 주세요.'
    others = [p['id'] for p in view['players'] if p['alive'] and p['id'] != view['self']['id']]
    if not others:
        return '의견: 이번 차례에는 추가 주장을 하지 않겠습니다.'
    # Rotate using public turn count, without using hidden roles or inventing evidence.
    count = sum(e['type'] in SPEECH_TYPES and e['data'].get('actor') == view['self']['id'] for e in view['events'])
    target = others[count % len(others)]
    questions = ['직접 본 내용과 다른 사람에게 들은 내용을 구분해 줄 수 있나요?',
                 '가장 최근 판단을 바꾼 근거가 무엇인지 말해 줄 수 있나요?',
                 '위치를 목격했다면 어느 시점의 기록인지 알려줄 수 있나요?']
    return f'의견: {target}, {questions[count % len(questions)]}'
