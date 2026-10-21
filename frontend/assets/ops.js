'use strict';
const $=id=>document.getElementById(id);
const labels={memory:'기억 검색',generate:'응답 생성',validate_evidence:'근거 검증',research:'재검색',repair:'수정 요청',fallback:'기본 행동'};
const statusNames={complete:'완료',running:'진행 중',fallback:'기본 행동',error:'오류',rejected:'검사 미통과'};
let selected=null,latest=null,paused=false,inflight=false;
const fmt=(n,d=0)=>n==null?'—':Number(n).toLocaleString('ko-KR',{maximumFractionDigits:d});
function element(tag,text,cls){const e=document.createElement(tag);if(text!=null)e.textContent=text;if(cls)e.className=cls;return e}
function render(d){
 latest=d;const t=d.totals,p=d.inference,m=d.mlflow;
 const metrics=[['서버 상태',d.ready?'정상':'준비 중',`가동 ${fmt(d.uptime_seconds)}초 · 메모리에 있는 방 ${Object.values(d.rooms).reduce((a,b)=>a+b,0)}개`],['AI 실행 / 대기',`${p.active} / ${p.waiting}`,`동시 판단 ${p.capacity}개 · 대기 최대 ${p.max_waiters}개`],['기록된 입력 / 출력 토큰',`${fmt(t.input_tokens||0)} / ${fmt(t.output_tokens||0)}`,`사용량 확인 ${t.usage_known_calls||0} / API 호출 ${t.api_calls||0}회`],['최근 판단 지연 P95',d.recent_p95_seconds==null?'—':`${fmt(d.recent_p95_seconds,3)}초`,`최근 ${d.retained_decisions}회 · 판단 실패 ${t.failed||0}회`]];
 $('cards').replaceChildren(...metrics.map(([label,value,sub])=>{const e=element('div',null,'card');e.append(element('div',label,'label'),element('strong',value),element('div',sub,'sub'));return e}));
 $('resources').replaceChildren(...[`기본 행동 ${t.fallback||0}회`,`수정·재시도 ${t.retries||0}회`,`고정 행동 호출 생략 ${t.forced||0}회`,`프로세스 최대 메모리 ${fmt(d.peak_rss_bytes/1048576,1)} MiB`,`MCP/검색 ${d.memory.closed?'닫힘':d.memory.backend}`,`토큰 캐시 ${d.memory.token_cache_enabled?'사용':'기본 미사용'}`].map(v=>element('span',v)));
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
 const link=$('mlflow-link');link.hidden=!m.uri;if(m.uri)link.href=m.uri+(m.experiment_id?`/#/experiments/${m.experiment_id}/runs?workflowType=machine_learning`:'');
 $('integrations').textContent=`MLflow ${m.state} · 전송 ${m.sent||0}건 · 대기 ${m.pending||0}/${m.capacity||0}건 · 실패 ${m.failures||0}회 · 누락 ${m.dropped||0}건 / LangSmith ${d.tracing.langsmith_configured?'추적 설정 켜짐':'추적 설정 꺼짐'}`;
}
async function refresh(){if(inflight)return;inflight=true;try{const r=await fetch('/api/ops',{cache:'no-store',signal:AbortSignal.timeout(5000)});if(!r.ok)throw Error(r.status);render(await r.json());$('connection').textContent='● 서버 연결됨';$('connection').className='';$('updated').textContent=`갱신 ${new Date().toLocaleTimeString('ko-KR')}`;}catch(e){$('connection').textContent='연결 실패 · 기존 수치는 마지막 수신 값';$('connection').className='off';}finally{inflight=false}}
$('refresh').onclick=refresh;$('pause').onclick=()=>{paused=!paused;$('pause').textContent=paused?'자동 갱신 멈춤':'자동 갱신 중';if(!paused)refresh()};
setInterval(()=>{if(!paused&&!document.hidden)refresh()},3000);refresh();
