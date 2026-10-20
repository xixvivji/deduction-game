const {createApp,nextTick}=Vue;
createApp({
 data(){return {name:'',code:'',size:7,state:null,session:null,connected:false,loading:false,error:'',text:'',target:'',location:'광장',now:Date.now(),socket:null,retry:null,ticker:null}},
 computed:{
 phaseName(){return ({lobby:'마을 대기실',night:'밤의 행동',council:'밤의 비밀 협의',discussion:'낮의 토론',vote:'추방 투표',finished:'사건의 끝'})[this.state?.phase]||'마을'},
 participants(){if(this.state.players.length)return this.state.players;return Array.from({length:this.state.size},(_,i)=>({id:'p'+i,alive:true,controller:this.state.members['p'+i]?'human':'ai'}))},
 visibleEvents(){return this.state.events||[]},targets(){return (this.state.action?.schema.properties.target?.enum||[]).filter(x=>x!==null)},
 seconds(){return Math.max(0,Math.ceil((this.state.deadline*1000-this.now)/1000))},
 roleDescription(){return ({wolf:'정체를 숨기고 살아남으세요. 밤에는 동료와 같은 공격 대상을 선택해야 합니다.',seer:'밤마다 한 명의 진영을 조사할 수 있습니다. 언제 정보를 공개할지 신중히 결정하세요.',citizen:'목격과 발언을 비교하고 늑대를 찾아내세요.'})[this.state.self.role]||'게임이 시작되면 비공개로 역할을 알려드립니다.'},
 waitingText(){if(this.state.status==='paused')return '게임이 일시 중단되었습니다.';if(this.state.phase==='council'&&this.state.self.role!=='wolf')return '늑대들의 밤 협의가 진행 중입니다.';if(this.state.phase==='finished')return '';if(this.state.phase==='lobby')return '친구에게 방 코드를 공유하세요.';if(this.state.players.find(p=>p.id===this.state.self.id)?.alive===false)return '관전 중입니다. 게임이 끝나면 모든 기록이 공개됩니다.';return this.state.active?this.playerName(this.state.active)+' 님의 차례를 기다려 주세요.':'다음 단계로 이동 중입니다.'},
 winnerText(){return ({wolf:'늑대 진영 승리',village:'시민 진영 승리',draw:'무승부'})[this.state.winner]||''}
 },
 methods:{
 playerName(id){return id?(this.state?.members[id]||'AI '+id.replace('p','')):'참가자'},roleName(r){return ({wolf:'늑대',seer:'조사자',citizen:'시민'})[r]||r},
 async api(path,body){const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json',...(this.session?{Authorization:'Bearer '+this.session.token}:{})},body:body===undefined?undefined:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'요청 내용을 확인해 주세요.');return data},
 async perform(fn){this.loading=true;try{await fn()}catch(e){this.error=e.message}finally{this.loading=false}},
 async create(){await this.perform(async()=>{this.session=await this.api('/api/rooms',{name:this.name.trim(),size:this.size});this.saveSession();await this.connect()})},
 async join(){await this.perform(async()=>{this.session=await this.api('/api/rooms/'+encodeURIComponent(this.code.trim())+'/join',{name:this.name.trim()});this.saveSession();await this.connect()})},
 saveSession(){sessionStorage.setItem('night-testimony',JSON.stringify(this.session))},
 async connect(){if(!this.session)return;this.state=await this.api('/api/rooms/'+this.session.room_id);this.openSocket()},
 openSocket(){if(!this.session)return;const sock=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/ws/'+this.session.room_id);this.socket=sock;sock.onopen=()=>{sock.send(JSON.stringify({token:this.session.token}));this.connected=true};sock.onmessage=async e=>{const previous=this.state?.events?.length;this.state=JSON.parse(e.data);if(previous!==this.state.events.length){await nextTick();const log=this.$refs.log;if(log)log.scrollTop=log.scrollHeight}};sock.onclose=()=>{this.connected=false;if(this.session)this.retry=setTimeout(()=>this.openSocket(),2000)}},
 async start(){await this.perform(async()=>{this.state=await this.api('/api/rooms/'+this.session.room_id+'/start',{})})},
 async resume(){await this.perform(async()=>{this.state=await this.api('/api/rooms/'+this.session.room_id+'/resume',{})})},
 async submit(){await this.perform(async()=>{const spec=this.state.action;const payload={};if(spec.schema.properties.target)payload.target=this.target||null;if(spec.schema.properties.text)payload.text=this.text.trim()||'판단을 보류합니다.';if(spec.schema.properties.location)payload.location=this.location;this.state=await this.api('/api/rooms/'+this.session.room_id+'/commands',{command_id:crypto.randomUUID?crypto.randomUUID():Date.now()+'-'+Math.random(),version:this.state.version,kind:spec.kind,payload});this.text='';this.target=''})},
 async copyCode(){try{await navigator.clipboard.writeText(this.state.room_id);this.error='방 코드를 복사했습니다.'}catch(e){this.error='방 코드: '+this.state.room_id}},
 leave(){this.session=null;clearTimeout(this.retry);if(this.socket)this.socket.close();sessionStorage.removeItem('night-testimony');this.state=null},
 eventText(e){const d=e.data;if(d.text)return d.text+(e.type==='비밀 협의'?' · 제안: '+(d.target?this.playerName(d.target):'보류'):'');if(e.type==='목격')return this.playerName(d.player)+' 님을 '+d.location+'에서 보았습니다.';if(e.type==='조사')return this.playerName(d.target)+' 님은 '+(d.team==='wolf'?'늑대':'시민')+' 진영입니다.';if(e.type==='밤 결과')return d.victim?this.playerName(d.victim)+' 님이 밤에 사망했습니다.':'모두 무사히 아침을 맞았습니다.';if(e.type==='투표 결과')return (d.expelled?this.playerName(d.expelled)+' 님이 추방되었습니다.':'동률 또는 기권으로 추방하지 않았습니다.')+' '+Object.entries(d.votes).map(([p,t])=>this.playerName(p)+' → '+(t?this.playerName(t):'기권')).join(' / ');if(e.type==='게임 종료')return this.winnerText;return d.day?d.day+'일차가 진행됩니다.':''},
 download(){const blob=new Blob([JSON.stringify(this.state,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='game-'+this.state.room_id+'.json';a.click();URL.revokeObjectURL(url)}
 },
 mounted(){this.ticker=setInterval(()=>this.now=Date.now(),500);try{this.session=JSON.parse(sessionStorage.getItem('night-testimony'));if(this.session)this.perform(()=>this.connect())}catch(e){sessionStorage.removeItem('night-testimony')}},
 beforeUnmount(){clearInterval(this.ticker);clearTimeout(this.retry);this.session=null;this.socket?.close()}
}).mount('#app');
