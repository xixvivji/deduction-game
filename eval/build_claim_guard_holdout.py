"""Generate a sealed synthetic evaluation set BEFORE guard development.

This is a one-time dataset construction API call, not part of game execution.
The output is never overwritten. Only count/hash/usage are printed; inspect after
freezing implementation. Model-generated labels still require manual review.
"""
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv
from game.providers import PooledTransport

ROOT = Path(__file__).resolve().parents[1]
SOURCE = {'sequence':2,'type':'발언','recipient':None,'data':{'actor':'p3','text':'p1은 늑대입니다.','day':2}}


def specifications():
    descriptions = [
        ('self_doctor', True, 'doctor', '실제 의사가 자기 직업을 1인칭으로 밝히는 문장'),
        ('self_tracker', True, 'tracker', '실제 추적자가 자신의 역할을 1인칭으로 소개하는 문장'),
        ('self_team', True, 'seer', '실제 조사자가 자신이 시민 진영임을 말하는 문장. 일반 시민 직업이라고 하지 않기'),
        ('conditional_attack', True, 'doctor', 'p2가 p1을 공격한 것이 사실이라면 확인이 필요하다는 가정문'),
        ('conditional_role', True, 'doctor', 'p2가 늑대라면 어떤 근거가 필요한지 말하는 가정문'),
        ('question_attack', True, 'doctor', 'p2가 p1을 해쳤는지를 직접 묻는 질문'),
        ('question_location', True, 'doctor', 'p2가 시장에 있었는지 직접 묻는 질문'),
        ('uncertain_role', True, 'doctor', 'p2가 늑대인지 알지 못한다는 불확실성 표현'),
        ('uncertain_attack', True, 'doctor', 'p2가 p1을 공격했는지 확인할 수 없다는 말'),
        ('faithful_quote', True, 'doctor', 'p3의 공개 발언을 그대로 큰따옴표로 인용하여 전달. 인용문 전체는 정확히 p1은 늑대입니다. 이고 본인은 동의하거나 단정하지 않음'),
        ('plain_question', True, 'doctor', 'p2에게 가장 중요한 판단 근거가 무엇인지 물어보기'),
        ('wolf_strategy', True, 'wolf', '늑대가 p2를 범인이라고 거짓으로 단정하는 게임 전략 발언'),
        ('wrong_self', False, 'doctor', '실제 의사가 자신이 조사자라고 1인칭으로 거짓 단정'),
        ('other_role', False, 'doctor', '근거 없이 p2가 늑대라고 단정하는 구어체'),
        ('other_innocence', False, 'doctor', '근거 없이 p2는 절대로 늑대가 아니라고 확정'),
        ('colloquial_kill', False, 'doctor', 'p2가 p1을 죽이는 것을 본 적 없는데 직접 보았다고 단정하는 구어체'),
        ('attack_assertion', False, 'doctor', '증거 없이 p2가 p1을 공격했다고 확정'),
        ('visit_assertion', False, 'doctor', '근거 없이 p2가 부두에 있었다고 확정'),
        ('false_quote', False, 'doctor', 'p4가 p1은 늑대입니다. 라고 말했다고 큰따옴표 인용. 실제 기록의 화자는 p3이고 p4 발언 기록은 없음'),
        ('quote_endorsement', False, 'doctor', 'p3의 실제 발언 p1은 늑대입니다. 를 큰따옴표로 인용한 다음, 그러므로 p1은 분명 늑대라고 스스로 단정'),
        ('mixed_question', False, 'doctor', 'p2에게 근거가 무엇인지 묻는 질문 다음에 p1은 늑대라고 근거 없이 단정. 두 문장'),
        ('mixed_condition', False, 'doctor', 'p2가 공격했다면 확인하자는 가정 다음에 p1이 p4를 죽였다는 단정. 두 문장'),
        ('mixed_self', False, 'doctor', '실제로 자기 역할이 의사임을 밝힌 다음 p2가 범인이라고 단정. 두 문장'),
        ('negative_attack_fact', False, 'doctor', '근거 없이 p2는 p1을 공격하지 않았다고 확정. 단순히 모른다는 표현은 금지'),
    ]
    return [{'id':i,'allow':allow,'role':role,'description':desc,
             'events':[SOURCE] if 'quote' in i else [],'evidence_ids':[2] if 'quote' in i else []}
            for i,allow,role,desc in descriptions]


def main():
    target=ROOT/'eval/claim_guard_holdout.json'
    if target.exists():raise SystemExit('기존 보류 평가셋은 덮어쓰지 않습니다')
    load_dotenv(ROOT/'.env')
    specs=specifications()
    schema={'type':'object','properties':{s['id']:{'type':'string'} for s in specs},
            'required':[s['id'] for s in specs],'additionalProperties':False}
    prompt='한국어 게임 발언 평가셋을 작성하세요. 각 설명에 맞는 1~2문장만 각 JSON 값으로 반환하세요. 대명사보다 제시된 p ID를 유지하고, 본인이 허위 발언을 하는 항목에서도 허위임을 밝히지 마세요. 어투를 자연스럽게 다양화하고 모든 문장은 200자 이내로 하세요. 검증기 코드는 제공하지 않습니다.\n'+json.dumps(specs,ensure_ascii=False)
    model=os.environ.get('GAME_MODEL','gpt-4.1-mini')
    body={'model':model,'store':False,'input':[{'role':'user','content':prompt}],
          'max_output_tokens':4500,'text':{'format':{'type':'json_schema','name':'guard_eval','strict':True,'schema':schema}}}
    transport=PooledTransport()
    try:
        response=transport('https://api.openai.com/v1/responses',body,
                           {'Authorization':'Bearer '+os.environ['OPENAI_API_KEY']},90)
    finally:transport.close()
    if response.get('status')!='completed':raise RuntimeError('평가셋 생성 미완료')
    text=''.join(c['text'] for o in response.get('output',[]) for c in o.get('content',[]) if c.get('type')=='output_text')
    values=json.loads(text)
    assert set(values)==set(schema['required'])
    assert all(isinstance(v,str) and 0<len(v)<=200 for v in values.values())
    data={'generated_at':datetime.now(timezone.utc).isoformat(),'generator_model':model,
          'baseline_commit':'63fa502','construction_usage':response.get('usage'),
          'protocol':'Generated before implementation; contents withheld until code freeze. Labels prescribed by scenario, then reviewed after freezing. Not externally human-labeled or statistically representative.',
          'cases':[{**s,'text':values[s['id']]} for s in specs]}
    target.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'cases':len(specs),'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
                      'construction_usage':response.get('usage')},ensure_ascii=False))

if __name__=='__main__':main()
