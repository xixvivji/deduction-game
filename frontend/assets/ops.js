'use strict';
const $=id=>document.getElementById(id);
const labels={memory:'기억 검색',generate:'응답 생성',validate_evidence:'근거 검증',research:'재검색',repair:'수정 요청',fallback:'기본 행동'};
const phaseNames={queue:'실행 대기',inference:'AI 판단 · 검색/생성/검증 포함',commit_wait:'방 잠금 대기',apply:'게임 규칙 검사·적용',bookkeeping:'이력·사용량 정리',persist:'DB 저장'};
const actionNames={completed:'일반 행동 반영',fallback_completed:'기본 행동 반영',failed:'실패',cancelled:'취소'};
const statusNames={complete:'완료',running:'진행 중',fallback:'기본 행동',error:'오류',rejected:'검사 미통과'};
let selected=null,latest=null,paused=false,inflight=false,opsToken='';
const fmt=(n,d=0)=>n==null?'—':Number(n).toLocaleString('ko-KR',{maximumFractionDigits:d});
function element(tag,text,cls){const e=document.createElement(tag);if(text!=null)e.textContent=text;if(cls)e.className=cls;return e}
function renderActions(a){
 if(!a)return;
 const t=a.totals,n=t.finalized||0,completed=t.completed||0;
 const metrics=[['서버 반영 완료율',n?`${fmt(a.completed_rate*100,1)}%`:'—',`종료 ${n}건 중 저장 완료 ${completed}건 · 기본 행동 포함`],['완료 행동 지연 P95',a.recent_completed_p95_seconds==null?'—':`${fmt(a.recent_completed_p95_seconds,3)}초`,`최근 완료 ${a.recent_completed_samples}건 · P50 ${fmt(a.recent_completed_p50_seconds,3)}초`],['실패 / 취소',`${t.failed||0} / ${t.cancelled||0}`,`현재 진행 ${a.active.length}건 · 실패는 완료 지연에서 제외`],['반영 1건당 확인 토큰',fmt(a.known_tokens_per_completed_action,1),`사용량 확인 ${t.usage_known_calls||0} / ${t.api_calls||0}호출 · 미확인 사용량 제외`]];
 $('action-cards').replaceChildren(...metrics.map(([label,value,sub])=>{const e=element('div',null,'card');e.append(element('div',label,'label'),element('strong',value),element('div',sub,'sub'));return e}));
 $('action-counts').replaceChildren(...[`일반 행동 반영 ${t.regular_completed||0}건`,`기본 행동 반영 ${t.fallback_completed||0}건`,`수정·재시도 ${t.retries||0}회`,...a.active.slice(0,8).map(x=>`${x.id.slice(0,6)} · ${phaseNames[x.phase]} · ${fmt(x.seconds,1)}초`)].map(x=>element('span',x)));
 const total=Object.values(a.recent_phase_seconds).reduce((x,y)=>x+y,0);
 $('action-phases').replaceChildren(...Object.entries(phaseNames).map(([key,label])=>{const row=element('div',null,'phasebar'),bar=element('div',null,'track'),fill=element('span'),share=total?a.recent_phase_seconds[key]/total:0;fill.style.width=`${share*100}%`;bar.append(fill);row.append(element('span',label),bar,element('span',`${fmt(a.recent_phase_mean_seconds[key]==null?null:a.recent_phase_mean_seconds[key]*1000,1)} ms · ${fmt(share*100,1)}%`));return row}));
 $('action-scope').textContent=`최근 종료 ${a.retained_actions}/${a.history_limit}건의 구간 평균·누적 시간 비중입니다. 실패·취소 포함. AI 판단 내부의 검색·생성 시간은 아래 흐름에서 확인하세요.`;
 const rows=a.recent.map(x=>{const row=element('tr');[x.id.slice(0,6),actionNames[x.status],`${fmt(x.seconds,3)}초`,x.failure_phase?phaseNames[x.failure_phase]:'—',`${x.applied?'적용':'미적용'} / ${x.persisted?'저장':'미저장'}`].forEach(value=>row.append(element('td',value)));return row});
 if(!rows.length){const row=element('tr'),cell=element('td','아직 완료된 서버 행동 기록이 없습니다. 게임을 진행하면 표시됩니다.');cell.colSpan=5;row.append(cell);rows.push(row)}
 $('action-table').replaceChildren(...rows);
}
function comparisonCard(label,before,after,note){
 const card=element('div',null,'comparison-card'),nums=element('div',null,'numbers');
 nums.append(element('strong',before,'before'),element('span','→','arrow'),element('strong',after));
 card.append(element('div',label,'label'),nums,element('div',note,'sub'));return card;
}
function renderReliability(data){
 const replay=data?.scripted,live=data?.live,q=data?.quality;
 $('reliability-cards').replaceChildren();$('live-eval-table').replaceChildren();$('mcp-evaluation').replaceChildren();$('quality-checks').replaceChildren();
 if(replay?.status==='measured'){
  const b=replay.before,a=replay.after;
  $('reliability-scope').textContent=`개발용 합성 상황 8개 × ${replay.config.repeats}회 · 고정 응답 ${a.runs}회 · ${new Date(replay.created_at).toLocaleString('ko-KR')} · ${replay.source_current?'현재 AI 코드와 일치':'주의: 측정 후 AI 코드 변경됨'}. 독립 평가셋·게임 승률이 아닙니다.`;
  $('reliability-cards').append(comparisonCard('재검색 횟수',b.research_calls,a.research_calls,'고정 응답 · 같은 입력'),comparisonCard('모의 모델 생성 횟수',b.model_calls,a.model_calls,'외부 API 호출 수와 구분'),comparisonCard('기대 행동 일치',`${b.correct}/${b.runs}`,`${a.correct}/${a.runs}`,`정상 응답 오차단 ${b.false_blocks}→${a.false_blocks} · 기권 대체 ${b.fallbacks}→${a.fallbacks}`));
  const m=replay.mcp;
  if(m?.status==='measured')$('mcp-evaluation').append(...[`관찰 창 ${m.window_seconds}초`,`복구 성공 ${m.before.recovered}/${m.before.trials} → ${m.after.recovered}/${m.after.trials}`,`성공 시험 평균 복구 ${m.before.mean_recovery_seconds==null?'미복구':fmt(m.before.mean_recovery_seconds,3)+'초'} → ${m.after.mean_recovery_seconds==null?'미복구':fmt(m.after.mean_recovery_seconds,3)+'초'}`,`실패한 검색 요청 ${m.before.failed_requests} → ${m.after.failed_requests}`].map(x=>element('span',x)));
 }else $('reliability-scope').textContent=replay?.status==='incomparable'?'입력 또는 설정이 달라 비교할 수 없습니다.':'전후 평가가 아직 완료되지 않았습니다.';
 if(live?.status==='measured'){
  const b=live.before,a=live.after;
  $('live-scope').textContent=`${live.config.provider} / ${live.config.model} · 조건별 ${a.runs}건 · ${live.source_current?'현재 AI 코드와 일치':'측정 후 AI 코드 변경됨'} · 시점을 나눠 실행한 소규모 표본으로 모델 출력·서버 부하가 달라질 수 있습니다. 인과적 성능 향상이나 요금 절감으로 단정하지 않습니다. 발언 오차단율·게임 승률은 이 시험의 평가 대상이 아닙니다.`;
  const metrics=[['전체 판단 평균 시간',b.mean_ms,a.mean_ms,'ms'],['전체 판단 P95',b.p95_ms,a.p95_ms,'ms'],['모델 생성 시도',b.model_calls,a.model_calls,'회'],['재검색',b.research_calls,a.research_calls,'회'],['확인된 입력 토큰',b.input_tokens,a.input_tokens,''],['확인된 출력 토큰',b.output_tokens,a.output_tokens,''],['기대 투표·기권 일치',`${b.correct}/${b.runs}`,`${a.correct}/${a.runs}`,''],['근거가 있는 투표의 기본 행동 대체',b.false_blocks,a.false_blocks,'건'],['기본 행동 대체',b.fallbacks,a.fallbacks,'건'],['인용 접근 범위 위반',b.scope_violations,a.scope_violations,'건'],['사용량 미확인 호출',b.usage_unknown_calls,a.usage_unknown_calls,'회']];
  $('live-eval-table').append(...metrics.map(([name,before,after,unit])=>{const row=element('tr');[name,...[before,after].map(x=>typeof x==='string'?x:`${fmt(x,2)} ${unit}`)].forEach(x=>row.append(element('td',x)));return row}));
 }else $('live-scope').textContent=live?.status==='incomparable'?'입력·모델·설정이 달라 비교할 수 없습니다.':'실제 API 전후 비교는 아직 측정하지 않았습니다.';
 $('quality-scope').textContent=q?.status==='not_measured'||!q?'검사 결과 미등록':`${q.source==='github_actions'?'GitHub Actions 실행':'로컬에서 CI와 같은 검사 실행'} · ${new Date(q.created_at).toLocaleString('ko-KR')} · ${q.status==='passed'?'전체 통과':'실패 있음'} · ${q.source_current?'검사한 코드와 일치':'검사 이후 코드 변경됨'}. 로컬 결과는 원격 CI 통과를 뜻하지 않습니다.`;
 for(const check of q?.checks||[])$('quality-checks').append(element('span',`${check.passed?'✓':'✕'} ${check.name} · ${check.tests==null?'문법 확인':check.tests+'개'} · ${fmt(check.seconds,2)}초`));
}
function render(d){
 latest=d;const t=d.totals,p=d.inference,m=d.mlflow;
 renderActions(d.actions);renderReliability(d.reliability);
 const metrics=[['서버 상태',d.ready?'정상':'준비 중',`가동 ${fmt(d.uptime_seconds)}초 · 메모리에 있는 방 ${Object.values(d.rooms).reduce((a,b)=>a+b,0)}개`],['AI 실행 / 대기',`${p.active} / ${p.waiting}`,`동시 판단 ${p.capacity}개 · 대기 최대 ${p.max_waiters}개`],['기록된 입력 / 출력 토큰',`${fmt(t.input_tokens||0)} / ${fmt(t.output_tokens||0)}`,`사용량 확인 ${t.usage_known_calls||0} / API 호출 ${t.api_calls||0}회`],['최근 판단 지연 P95',d.recent_p95_seconds==null?'—':`${fmt(d.recent_p95_seconds,3)}초`,`최근 ${d.retained_decisions}회 · 판단 실패 ${t.failed||0}회`]];
 $('cards').replaceChildren(...metrics.map(([label,value,sub])=>{const e=element('div',null,'card');e.append(element('div',label,'label'),element('strong',value),element('div',sub,'sub'));return e}));
 $('resources').replaceChildren(...[`기본 행동 ${t.fallback||0}회`,`수정·재시도 ${t.retries||0}회`,`고정 행동 호출 생략 ${t.forced||0}회`,`프로세스 최대 메모리 ${fmt(d.peak_rss_bytes/1048576,1)} MiB`,`MCP/검색 ${d.memory.connection?.state||(d.memory.closed?'닫힘':d.memory.backend)}`,`MCP 재연결 ${d.memory.connection?.reconnect_attempts||0}회 · 복구 ${d.memory.connection?.recoveries||0}회`,`MCP 대기 ${d.memory.connection?.pending||0}/${d.memory.connection?.capacity||0} · 최근 복구 ${fmt(d.memory.connection?.last_recovery_seconds,3)}초`,`재검색 ${t.research_calls||0}회 · 반복 실패 중단 ${t.repeated_failure_stops||0}회`,`토큰 캐시 ${d.memory.token_cache_enabled?'사용':'기본 미사용'}`].map(v=>element('span',v)));
 $('runtime-title').textContent='서비스 운영 · '+d.source;
 $('retention').textContent=`최근 ${d.retained_decisions}/${d.history_limit}회 보관`;
 const traces=[...d.active,...d.recent];if(!traces.some(x=>x.id===selected))selected=traces[0]?.id||null;
 $('traces').replaceChildren(...traces.map(tr=>{const btn=element('button',null,`trace ${tr.id===selected?'selected':''}`);const lhs=element('span');lhs.append(element('strong',`${tr.backend} · ${tr.id.slice(0,6)}`),element('small',new Date(tr.started_at*1000).toLocaleTimeString('ko-KR')));btn.append(lhs,element('span',`${statusNames[tr.status]} · ${tr.seconds==null?'진행 중':fmt(tr.seconds,3)+'초'}`,'badge'));btn.onclick=()=>{selected=tr.id;render(latest)};return btn}));
 if(!traces.length)$('traces').append(element('div','서버 시작 후 아직 AI 판단이 없습니다. 게임을 진행하면 실제 실행 기록이 나타납니다.','empty'));
 const tr=traces.find(x=>x.id===selected),visited=new Set((tr?.stages||[]).map(s=>s.node));
 $('flow').replaceChildren(...d.graph.nodes.map(node=>{const box=element('div',null,`node ${tr?.status==='running'&&tr.current_node===node?'active':visited.has(node)?'visited':''}`);box.append(element('strong',labels[node]),element('small',`누적 ${d.nodes[node]||0}회`));return box}));
 $('detail-title').textContent=tr?`판단 ${tr.id.slice(0,6)} · ${statusNames[tr.status]}`:'판단을 선택하세요';$('detail').replaceChildren();
 if(tr){$('detail').append(element('p',`생성 시도 ${tr.attempts??'…'}회 · API 호출 ${tr.api_calls??'…'}회 · 관측 ${tr.visible_events??'…'} / 선택 ${tr.selected_events??'…'}건`,'sub'));(tr.stages||[]).forEach((s,i)=>{const row=element('div',null,'stagerow');row.append(element('b',String(i+1).padStart(2,'0')),element('span',labels[s.node]),element('span',statusNames[s.status],s.status),element('time',s.seconds==null?'…':fmt(s.seconds*1000,1)+' ms'));$('detail').append(row)});if(!tr.stages.length)$('detail').append(element('p',tr.forced?'고정 행동이라 모델과 그래프 실행을 생략했습니다.':'첫 단계 시작을 기다리고 있습니다.','sub'))}
 const b=d.evaluation.before,a=d.evaluation.after;$('eval-scope').textContent=`문장 범위 검증 v2 · 같은 합성 입력 ${d.evaluation.cases}건 · ${d.evaluation.scope}`;
 $('comparison').replaceChildren(...[['올바른 판정',b.correct,a.correct,'22개 중 기대한 허용·차단과 일치'],['근거 없는 단정을 놓침',b.missed_blocks,a.missed_blocks,'차단해야 하는 11건 기준 · 적을수록 좋음'],['정상 발언을 잘못 차단',b.false_blocks,a.false_blocks,'허용해야 하는 11건 기준 · 개선되지 않음']].map(([label,before,after,sub])=>{const card=element('div',null,'comparison-card'),nums=element('div',null,'numbers');nums.append(element('strong',before,'before'),element('span','→','arrow'),element('strong',after));card.append(element('div',label,'label'),nums,element('div',sub,'sub'));return card}));
 $('eval-table').replaceChildren(...[['정상 발언 수정 재시도 합계',`${b.allowed_replay_retries}회`,`${a.allowed_replay_retries}회`],['근거 없는 단정 수정 재시도 합계',`${b.unsupported_replay_retries}회`,`${a.unsupported_replay_retries}회`],['검사 시간 · 사례별 중앙값 평균',`${b.mean_case_median_check_ms} ms`,`${a.mean_case_median_check_ms} ms`]].map(values=>{const row=element('tr');values.forEach(v=>row.append(element('td',v)));return row}));
 const link=$('mlflow-link');link.hidden=!m.uri;if(m.uri)link.href=(m.public_uri||m.uri)+(m.experiment_id?`/#/experiments/${m.experiment_id}/runs?workflowType=machine_learning`:'');
 $('integrations').textContent=`MLflow ${m.state} · 전송 ${m.sent||0}건 · 대기 ${m.pending||0}/${m.capacity||0}건 · 실패 ${m.failures||0}회 · 누락 ${m.dropped||0}건 / LangSmith ${d.tracing.langsmith_configured?'추적 설정 켜짐':'추적 설정 꺼짐'}`;
}
async function refresh(){if(inflight)return;inflight=true;try{const r=await fetch('/api/ops',{cache:'no-store',headers:opsToken?{'X-Ops-Token':opsToken}:{},signal:AbortSignal.timeout(5000)});if(r.status===401){$('ops-auth').hidden=false;$('connection').textContent='운영 접근 키 입력 필요';if(opsToken)$('auth-error').textContent='접근 키를 확인해 주세요.';return}if(!r.ok)throw Error(r.status);$('ops-auth').hidden=true;$('auth-error').textContent='';render(await r.json());$('connection').textContent='● 서버 연결됨';$('connection').className='';$('updated').textContent=`갱신 ${new Date().toLocaleTimeString('ko-KR')}`;}catch(e){$('connection').textContent='연결 실패 · 기존 수치는 마지막 수신 값';$('connection').className='off';}finally{inflight=false}}
$('ops-auth').onsubmit=e=>{e.preventDefault();opsToken=$('ops-token').value.trim();$('ops-token').value='';refresh()};
$('refresh').onclick=refresh;$('pause').onclick=()=>{paused=!paused;$('pause').textContent=paused?'자동 갱신 멈춤':'자동 갱신 중';if(!paused)refresh()};
setInterval(()=>{if(!paused&&!document.hidden&&$('ops-auth').hidden)refresh()},3000);refresh();
