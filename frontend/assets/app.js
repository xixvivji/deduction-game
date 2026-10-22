const {createApp,nextTick}=Vue;
// Synthesized ambience: no external media, starts only after an explicit click.
const soundscape={
 context:null,master:null,ambient:null,night:false,volume:.25,enabled:false,
 async enable(){
  if(!this.context){
   this.context=new (window.AudioContext||window.webkitAudioContext)();
   this.master=this.context.createGain();this.master.connect(this.context.destination);
   const buffer=this.context.createBuffer(1,this.context.sampleRate*4,this.context.sampleRate);
   const samples=buffer.getChannelData(0);for(let i=0;i<samples.length;i++)samples[i]=(Math.random()*2-1)*.18;
   const noise=this.context.createBufferSource();noise.buffer=buffer;noise.loop=true;
   this.filter=this.context.createBiquadFilter();this.filter.type='lowpass';this.filter.frequency.value=650;
   this.ambient=this.context.createGain();this.ambient.gain.value=.3;
   noise.connect(this.filter);this.filter.connect(this.ambient);this.ambient.connect(this.master);noise.start();
  }
  await this.context.resume();this.enabled=true;this.level();
 },
 level(){if(this.master)this.master.gain.setTargetAtTime(this.enabled&&!document.hidden?this.volume:0,this.context.currentTime,.15)},
 disable(){this.enabled=false;this.level()},
 phase(night){this.night=night;if(this.filter)this.filter.frequency.setTargetAtTime(night?850:350,this.context.currentTime,1)},
 tone(frequency,duration=.3,delay=0,type='sine',gain=.15){
  if(!this.enabled||document.hidden||!this.context)return;
  const t=this.context.currentTime+delay,o=this.context.createOscillator(),g=this.context.createGain();
  o.type=type;o.frequency.value=frequency;g.gain.setValueAtTime(0,t);g.gain.linearRampToValueAtTime(gain,t+.015);g.gain.exponentialRampToValueAtTime(.001,t+duration);
  o.connect(g);g.connect(this.master);o.start(t);o.stop(t+duration+.02);o.onended=()=>{o.disconnect();g.disconnect()};
 },
 bell(){this.tone(523,.9);this.tone(784,1.2,.12);this.tone(1046,1.3,.25,'sine',.06)},
 step(){this.tone(100,.12,0,'triangle');this.tone(80,.12,.2,'triangle')},
 tick(){if(this.night){this.tone(3200,.08,0,'sine',.025);this.tone(3400,.08,.14,'sine',.02)}else this.tone(950,.05,0,'triangle',.015)},
 close(){this.context?.close();this.context=null;this.enabled=false}
};
const VillageScene={
 props:{people:{type:Array,default:()=>[]},phase:String,active:String,names:Object,selfId:String,winner:String,reduced:Boolean,role:String,journey:String,selected:String},
 computed:{night(){return !['discussion','vote'].includes(this.phase)},secret(){return this.phase==='council'&&this.role==='wolf'}},
 methods:{position(i){const a=(i/this.people.length)*Math.PI*2-Math.PI/2;return {left:(this.people.length>7?14+(i%4)*24:50+Math.cos(a)*31)+'%',top:(this.people.length>7?36+Math.floor(i/4)*25:65+Math.sin(a)*21)+'%','--delay':(-i*.73)+'s','--hair':['#322a28','#543b2d','#cfbc91','#3c303c','#202e2a','#704934','#a39a8e'][i%7],'--coat':['#a47367','#839e9a','#c4a66d','#8d8caf','#7c9876','#bc8c6b','#9c8193'][i%7]}},name(p){return this.names?.[p.id]||'AI '+p.id.replace('p','')}},
 template:document.getElementById('village-template').innerHTML
};
createApp({
 components:{VillageScene,ExplorationMap},
 data(){return {realtimeEnabled:true,exploreEnabled:true,moveBusy:false,questionTarget:'',quoteIds:[],sound:false,volume:25,showPeople:false,showRecords:true,selectedPerson:'',journey:'',transitionText:'',transitionTimer:null,journeyTimer:null,ambientTicker:null,reduced:window.matchMedia('(prefers-reduced-motion: reduce)').matches,name:'',code:'',size:7,state:null,session:null,connected:false,loading:false,error:'',text:'',target:'',location:'광장',now:Date.now(),socket:null,retry:null,ticker:null}},
 computed:{
 latestAnswer(){return [...this.visibleEvents].reverse().find(e=>e.type==='답변')},
answerQuestion(){return this.visibleEvents.find(e=>e.sequence===this.latestAnswer?.data.question_id)},
latestSpeech(){return [...(this.state?.events||[])].reverse().find(e=>['발언','질문','답변'].includes(e.type)&&e.recipient===null)},
 publicQuotes(){return this.visibleEvents.filter(e=>e.recipient===null&&['발언','질문','답변','투표 결과','밤 결과'].includes(e.type))},
 latestResult(){return [...(this.state?.events||[])].reverse().find(e=>['투표 결과','밤 결과'].includes(e.type))},
 selectedRecords(){return this.visibleEvents.filter(e=>e.data.actor===this.selectedPerson||e.data.player===this.selectedPerson||e.data.target===this.selectedPerson)},
 phaseKey(){return this.state?this.state.room_id+':'+this.state.day+':'+this.state.phase:''},
 phaseName(){if(this.state?.realtime&&this.state.phase==='night')return '탐색 전 준비';return ({lobby:'마을 대기실',explore:'마을 탐색',night:'밤의 행동',council:'밤의 비밀 협의',discussion:'낮의 토론',vote:'추방 투표',finished:'사건의 끝'})[this.state?.phase]||'마을'},
 participants(){if(this.state.players.length)return this.state.players;return Array.from({length:this.state.size},(_,i)=>({id:'p'+i,alive:true,controller:this.state.members['p'+i]?'human':'ai'}))},
 visibleEvents(){return this.state.events||[]},targets(){return (this.state.action?.schema.properties.target?.enum||[]).filter(x=>x!==null)},
 seconds(){return Math.max(0,Math.ceil((this.state.deadline*1000-this.now)/1000))},
 roleDescription(){if(this.state.realtime){const descriptions={wolf:'정체를 숨기고 시야 안의 대상에게 접근하세요. 2일차부터 근접 공격이 가능하며 공격 뒤 15초 동안 기다려야 합니다.',doctor:'탐색 전에 한 명을 보호합니다. 해당 탐색의 공격을 한 번 막으며 같은 사람을 연속 보호할 수 없습니다.',tracker:'생존자 한 명의 이전 탐색 마지막 장소를 확인합니다. 첫날에는 이전 기록이 없습니다.'};if(descriptions[this.state.self.role])return descriptions[this.state.self.role]}return ({tracker:'밤마다 다른 생존자 한 명의 방문 장소를 확인합니다. 장소만 알 수 있으며 역할이나 범행 여부는 알 수 없습니다.',coroner:'밤마다 이미 사망한 한 명의 정확한 역할을 확인합니다. 오늘 밤 새로 사망할 사람은 미리 선택할 수 없습니다.',wolf:'정체를 숨기고 살아남으세요. 밤에는 동료와 같은 공격 대상을 선택해야 합니다.',doctor:'밤마다 한 명을 보호해 늑대의 공격을 막습니다. 자신도 보호할 수 있지만 같은 사람을 연속 보호할 수 없습니다.',seer:'밤마다 한 명의 진영을 조사할 수 있습니다. 언제 정보를 공개할지 신중히 결정하세요.',citizen:'목격과 발언을 비교하고 늑대를 찾아내세요.'})[this.state.self.role]||'게임이 시작되면 비공개로 역할을 알려드립니다.'},
 waitingText(){if(this.state.phase==='explore')return this.state.realtime?'지도를 클릭해 이동하세요. 시체 신고 또는 시간 종료 후 회의가 열립니다.':'지도를 클릭해 이동하세요. 탐색이 끝나면 밤 능력을 사용합니다.';if(this.state.status==='paused')return '게임이 일시 중단되었습니다.';if(this.state.phase==='council'&&this.state.self.role!=='wolf')return '늑대들의 밤 협의가 진행 중입니다.';if(this.state.phase==='finished')return '';if(this.state.phase==='lobby')return '친구에게 방 코드를 공유하세요.';if(this.state.players.find(p=>p.id===this.state.self.id)?.alive===false)return '관전 중입니다. 게임이 끝나면 모든 기록이 공개됩니다.';return this.state.active?this.playerName(this.state.active)+' 님의 차례를 기다려 주세요.':'다음 단계로 이동 중입니다.'},
 winnerText(){return ({wolf:'늑대 진영 승리',village:'시민 진영 승리',draw:'무승부'})[this.state.winner]||''}
 },
 watch:{
 phaseKey(value,old){
  if(!value||!old)return;
  soundscape.phase(['night','council'].includes(this.state.phase));soundscape.bell();
  this.transitionText=this.phaseName;clearTimeout(this.transitionTimer);
  this.transitionTimer=setTimeout(()=>this.transitionText='',2600);
 },
 volume(value){soundscape.volume=value/100;soundscape.level()},
 sound(value){if(!value)soundscape.disable()}
 },
 methods:{
 async fieldAction(kind,target){await this.perform(async()=>{this.state=await this.api('/api/rooms/'+this.session.room_id+'/field-action',{kind,target,day:this.state.day})})},
 async moveDirection(dx,dy){if(this.moveBusy||!this.connected||this.state?.phase!=='explore'||this.state.status!=='running')return;this.moveBusy=true;try{await this.api('/api/rooms/'+this.session.room_id+'/move',{dx,dy,day:this.state.day,sequence:Date.now()})}catch(e){/* A phase can advance before its websocket update arrives. */if(e.status!==409&&this.state?.phase==='explore')this.error=e.message}finally{this.moveBusy=false}},
 async toggleSound(){if(this.sound){this.sound=false;return}try{await soundscape.enable();soundscape.phase(['night','council'].includes(this.state?.phase));this.sound=true;soundscape.bell()}catch(e){this.error='이 브라우저에서 소리를 시작할 수 없습니다.'}},
 visibilityChanged(){soundscape.level()},
 selectPerson(id){this.selectedPerson=this.selectedPerson===id?'':id},
 playerName(id){return id?(this.state?.members[id]||'AI '+id.replace('p','')):'참가자'},roleName(r){return ({wolf:'늑대',seer:'조사자',doctor:'의사',tracker:'추적자',coroner:'검시관',citizen:'시민'})[r]||r},
 async api(path,body){const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json',...(this.session?{Authorization:'Bearer '+this.session.token}:{})},body:body===undefined?undefined:JSON.stringify(body)});const data=await response.json();if(!response.ok){const error=Error(typeof data.detail==='string'?data.detail:'요청 내용을 확인해 주세요.');error.status=response.status;throw error}return data},
 async perform(fn){this.loading=true;try{await fn()}catch(e){this.error=e.message}finally{this.loading=false}},
 async create(){await this.perform(async()=>{this.session=await this.api('/api/rooms',{name:this.name.trim(),size:this.size,explore:this.exploreEnabled,realtime:this.realtimeEnabled});this.saveSession();await this.connect()})},
 async join(){await this.perform(async()=>{this.session=await this.api('/api/rooms/'+encodeURIComponent(this.code.trim())+'/join',{name:this.name.trim()});this.saveSession();await this.connect()})},
 saveSession(){sessionStorage.setItem('night-testimony',JSON.stringify(this.session))},
 async connect(){if(!this.session)return;this.state=await this.api('/api/rooms/'+this.session.room_id);this.openSocket()},
 openSocket(){if(!this.session)return;const sock=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/ws/'+this.session.room_id);this.socket=sock;sock.onopen=()=>{sock.send(JSON.stringify({token:this.session.token}));this.connected=true};sock.onmessage=async e=>{const previous=this.state?.events?.length;this.state=JSON.parse(e.data);if(previous!==this.state.events.length){await nextTick();const log=this.$refs.log;if(log)log.scrollTop=log.scrollHeight}};sock.onclose=()=>{this.connected=false;if(this.session)this.retry=setTimeout(()=>this.openSocket(),2000)}},
 async start(){await this.perform(async()=>{this.state=await this.api('/api/rooms/'+this.session.room_id+'/start',{})})},
 async resume(){await this.perform(async()=>{this.state=await this.api('/api/rooms/'+this.session.room_id+'/resume',{})})},
 async submit(){await this.perform(async()=>{const spec=this.state.action;const payload={};if(spec.schema.properties.target)payload.target=this.target||null;if(spec.schema.properties.text)payload.text=this.text.trim()||'판단을 보류합니다.';if(spec.schema.properties.location)payload.location=this.state.self.explored_location||this.location;const asking=this.state.phase==='discussion'&&this.questionTarget;if(asking){if(this.quoteIds.length>3)throw Error('공개 기록은 최대 3개까지 인용할 수 있습니다.');if(!this.text.trim())throw Error('질문 내용을 입력해 주세요.');payload.target=this.questionTarget;payload.evidence_ids=this.quoteIds.map(Number)}this.state=await this.api('/api/rooms/'+this.session.room_id+'/commands',{command_id:crypto.randomUUID?crypto.randomUUID():Date.now()+'-'+Math.random(),version:this.state.version,kind:asking?'question':spec.kind,payload});if(payload.location&&!this.state.realtime){this.journey=payload.location;soundscape.step();clearTimeout(this.journeyTimer);this.journeyTimer=setTimeout(()=>this.journey='',2200)}else soundscape.tone(440,.2);this.text='';this.target='';this.questionTarget='';this.quoteIds=[]})},
 async copyCode(){try{await navigator.clipboard.writeText(this.state.room_id);this.error='방 코드를 복사했습니다.'}catch(e){this.error='방 코드: '+this.state.room_id}},
 leave(){this.selectedPerson='';this.journey='';this.transitionText='';clearTimeout(this.journeyTimer);clearTimeout(this.transitionTimer);this.session=null;clearTimeout(this.retry);if(this.socket)this.socket.close();sessionStorage.removeItem('night-testimony');this.state=null},
 eventText(e){const d=e.data;if(e.type==='시체 신고')return this.playerName(d.actor)+' 님이 '+d.location+'에서 '+this.playerName(d.target)+' 님의 시체를 신고했습니다.';if(e.type==='공격 목격')return d.second+'초: '+this.playerName(d.actor)+' 님이 '+this.playerName(d.target)+' 님을 공격하는 장면을 보았습니다.';if(e.type==='현장 공격')return this.playerName(d.target)+' 님을 공격했습니다.';if(e.type==='보호 발동')return this.playerName(d.target)+' 님의 보호가 공격을 한 번 막았습니다.';if(e.type==='준비 완료')return '역할 능력 준비가 끝났습니다. 현장 탐색을 시작합니다.';if(e.type==='회의 소집')return d.reason+' · 탐색을 마치고 회의를 시작합니다.';if(e.type==='발견 결과')return d.victims.length?'이번 탐색 사망자: '+d.victims.map(this.playerName).join(', '):'모두 무사히 회의에 모였습니다.';if(e.type==='탐색 종료')return '탐색이 끝났습니다. 마지막 위치에서 밤 행동을 시작합니다.';if(e.type==='탐색 목격')return d.day+'일차 탐색 '+d.second+'초: '+this.playerName(d.player)+' 님을 '+d.location+' 구역에서 보았습니다.';if(e.type==='추적'&&d.location==='기록 없음')return this.playerName(d.target)+' 님의 이전 탐색 기록이 없습니다.';if(e.type==='추적')return this.playerName(d.target)+' 님의 '+d.day+'일차 방문 장소는 '+d.location+'입니다.';if(e.type==='검시')return this.playerName(d.target)+' 님의 역할은 '+this.roleName(d.role)+'였습니다.';if(e.type==='질문')return this.playerName(d.target)+'에게 질문: '+d.text;if(d.text)return d.text+(e.type==='비밀 협의'?' · 제안: '+(d.target?this.playerName(d.target):'보류'):'');if(e.type==='목격')return this.playerName(d.player)+' 님을 '+d.location+'에서 보았습니다.';if(e.type==='보호 선택')return this.playerName(d.target)+' 님을 보호 대상으로 선택했습니다. 공격 차단 여부는 알 수 없습니다.';if(e.type==='조사')return this.playerName(d.target)+' 님은 '+(d.team==='wolf'?'늑대':'시민')+' 진영입니다.';if(e.type==='밤 결과')return d.victim?this.playerName(d.victim)+' 님이 밤에 사망했습니다.':'모두 무사히 아침을 맞았습니다.';if(e.type==='투표 결과')return (d.expelled?this.playerName(d.expelled)+' 님이 추방되었습니다.':'동률 또는 기권으로 추방하지 않았습니다.')+' '+Object.entries(d.votes).map(([p,t])=>this.playerName(p)+' → '+(t?this.playerName(t):'기권')).join(' / ');if(e.type==='게임 종료')return this.winnerText;return d.day?d.day+'일차가 진행됩니다.':''},
 citationText(id){const e=this.visibleEvents.find(e=>e.sequence===id&&e.recipient===null);return e?this.eventText(e):'공개 기록을 찾을 수 없습니다.'},
 download(){const blob=new Blob([JSON.stringify(this.state,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='game-'+this.state.room_id+'.json';a.click();URL.revokeObjectURL(url)}
 },
 mounted(){document.addEventListener('visibilitychange',this.visibilityChanged);this.ambientTicker=setInterval(()=>soundscape.tick(),3200);this.ticker=setInterval(()=>this.now=Date.now(),500);try{this.session=JSON.parse(sessionStorage.getItem('night-testimony'));if(this.session)this.perform(()=>this.connect())}catch(e){sessionStorage.removeItem('night-testimony')}},
 beforeUnmount(){document.removeEventListener('visibilitychange',this.visibilityChanged);clearInterval(this.ambientTicker);clearTimeout(this.transitionTimer);clearTimeout(this.journeyTimer);soundscape.close();clearInterval(this.ticker);clearTimeout(this.retry);this.session=null;this.socket?.close()}
}).mount('#app');
