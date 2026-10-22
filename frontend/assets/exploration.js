// Camera, fog and avatars must use the same interpolated frame.
const mapMotion = {
 blend(a,b,t){return a&&b?[a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t]:b},
 packet(world,previous,selfId,now,reduced){
  const old=new Map((previous.players||[]).map(p=>[p.id,[p.x,p.y]]));
  return {at:now,duration:reduced?0:100,selfFrom:previous.self,selfTo:world.self_position,
   players:world.players.map(p=>{const to=p.id===selfId&&world.self_position?world.self_position:[p.x,p.y];return {...p,from:old.get(p.id)||to,to}})};
 },
 sample(packet,now){
  const t=packet.duration?Math.max(0,Math.min(1,(now-packet.at)/packet.duration)):1;
  return {self:this.blend(packet.selfFrom,packet.selfTo,t),players:packet.players.map(p=>{const xy=this.blend(p.from,p.to,t);return {id:p.id,x:xy[0],y:xy[1],moving:p.moving}})};
 }
};
// Decorative terrain is derived once from the public map, never from player state.
const mapPalette = ['#d5b477','#80a89a','#bb8580','#9c93be','#93ab78','#b99174','#81a1b6','#c49aaf','#aeb38a','#99a6c5','#c7a475'];
function mapScenery(world){
 const blocked=[...world.roads,...world.courts,...world.buildings];
 const trees=[];
 for(let y=65;y<world.height-30;y+=88)for(let x=48;x<world.width-65;x+=84){
  const seed=(x*17+y*31)%97,px=x+seed%23,py=y+seed%17;
  if(blocked.some(([a,b,w,h])=>px>a-34&&px<a+w+34&&py>b-22&&py<b+h+48))continue;
  trees.push({x:px,y:py,scale:.72+(seed%4)*.1,tone:seed%3});
 }
 return trees;
}
// Only server-filtered positions enter this component. No hidden player cache.
const ExplorationMap = {
 props:{world:Object,names:Object,selfId:String,running:Boolean,reduced:Boolean},
 emits:['move','act'],
 data(){return {keys:[],pad:[0,0],timer:null,frameId:null,motion:null,scenery:[],rendered:{self:null,players:[]}}},
 computed:{camera(){const w=800,h=500,p=this.rendered.self||[this.world.width/2,this.world.height/2];return [Math.max(0,Math.min(this.world.width-w,p[0]-w/2)),Math.max(0,Math.min(this.world.height-h,p[1]-h/2)),w,h].join(' ')},canMove(){return this.running&&!!this.world.self_position},direction(){return [Math.sign(Number(this.keys.includes('ArrowRight')||this.keys.includes('d'))-Number(this.keys.includes('ArrowLeft')||this.keys.includes('a'))+this.pad[0]),Math.sign(Number(this.keys.includes('ArrowDown')||this.keys.includes('s'))-Number(this.keys.includes('ArrowUp')||this.keys.includes('w'))+this.pad[1])]}},
 watch:{world:{immediate:true,handler(){this.syncMotion()}},reduced(){this.syncMotion()},canMove(value){if(!value)this.stop()}},
 methods:{
  syncMotion(){const now=performance.now();const previous=this.motion?mapMotion.sample(this.motion,now):this.rendered;this.motion=mapMotion.packet(this.world,previous,this.selfId,now,this.reduced);this.rendered=mapMotion.sample(this.motion,now)},
  animate(now){if(this.motion)this.rendered=mapMotion.sample(this.motion,now);this.frameId=requestAnimationFrame(this.animate)},
  name(id){return this.names?.[id]||'AI '+id.replace('p','')},
  label(id){return this.name(id).slice(0,8)+(id===this.selfId?' · 나':'')},
  labelWidth(id){return 14+[...this.label(id)].reduce((n,c)=>n+(c.charCodeAt(0)>255?11:6),0)},
  coat(id){return mapPalette[(Number(id.replace('p',''))||0)%mapPalette.length]},
  key(event,down){const key=event.key.length===1?event.key.toLowerCase():event.key;if(!['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','w','a','s','d'].includes(key))return;event.preventDefault();if(down&&!this.keys.includes(key))this.keys.push(key);if(!down)this.keys=this.keys.filter(k=>k!==key);this.send()},
  send(){if(this.canMove)this.$emit('move',...this.direction)},
  press(event,dx,dy){if(!this.canMove)return;event.currentTarget.setPointerCapture?.(event.pointerId);this.pad=[dx,dy];this.send()},
  stop(){this.keys=[];this.pad=[0,0];this.$emit('move',0,0)},
  visibility(){if(document.hidden)this.stop()}
 },
 mounted(){this.scenery=mapScenery(this.world);this.frameId=requestAnimationFrame(this.animate);this.timer=setInterval(()=>this.send(),100);window.addEventListener('blur',this.stop);document.addEventListener('visibilitychange',this.visibility)},
 beforeUnmount(){cancelAnimationFrame(this.frameId);clearInterval(this.timer);window.removeEventListener('blur',this.stop);document.removeEventListener('visibilitychange',this.visibility);this.stop()},
 template:`<section class="exploration-panel" :class="{'no-motion':reduced}" aria-label="실시간 마을 탐색">
 <div class="explore-heading"><div><span class="eyebrow">해 질 녘 · 함께 탐색</span><h2>{{world.location||'관전 중'}} <small>{{Math.ceil(world.remaining)}}초 남음</small></h2></div><strong class="explore-status">{{running?'탐색 진행 중':'연결 대기 / 일시 중단'}}</strong></div>
 <div class="explore-canvas" tabindex="0" role="group" aria-label="마을 이동 지도. 클릭 후 방향키 또는 WASD로 이동" @keydown="key($event,true)" @keyup="key($event,false)" @blur="stop">
 <svg class="village-map" :viewBox="camera" preserveAspectRatio="xMidYMid slice" role="img" aria-label="시야 안의 참가자와 마을 지도">
 <defs>
  <pattern id="forest-floor" width="96" height="96" patternUnits="userSpaceOnUse"><rect width="96" height="96" fill="#233b35"/><path d="M8 26q20-11 35 4M54 72q20-9 38 0M0 87l18-5" fill="none" stroke="#3d5548" stroke-width="2" opacity=".6"/><path d="M20 62l3-5 3 5M71 20l3-5 3 5" fill="none" stroke="#758465" opacity=".5"/><circle cx="46" cy="44" r="2" fill="#77836a" opacity=".3"/></pattern>
  <pattern id="stone-paving" width="40" height="32" patternUnits="userSpaceOnUse"><rect width="40" height="32" fill="#a5977b"/><path d="M0 0H40M0 16H40M20 0v16M0 16v16M40 16v16" stroke="#796f5d" stroke-width="1.2" opacity=".7"/><path d="M2 2h16M22 18h16" stroke="#c8b998" stroke-width="1" opacity=".65"/></pattern>
  <pattern id="roof-tiles" width="24" height="18" patternUnits="userSpaceOnUse"><rect width="24" height="18" fill="#41595b"/><path d="M0 0h24M0 9h24M12 0v9M0 9v9M24 9v9" fill="none" stroke="#263e43" stroke-width="2"/><path d="M2 2h8M14 11h8" stroke="#6b807b" opacity=".5"/></pattern>
  <linearGradient id="house-wall" x2="0" y2="1"><stop stop-color="#a18a6c"/><stop offset="1" stop-color="#6c5d4d"/></linearGradient>
  <radialGradient id="lantern-glow"><stop stop-color="#fbd28c" stop-opacity=".32"/><stop offset="1" stop-color="#efb56a" stop-opacity="0"/></radialGradient>
  <radialGradient id="fog-edge"><stop offset=".72" stop-color="black"/><stop offset="1" stop-color="white"/></radialGradient>
  <mask id="explore-fog"><rect :width="world.width" :height="world.height" fill="white"/><circle v-if="rendered.self" :cx="rendered.self[0]" :cy="rendered.self[1]" :r="world.sight" fill="url(#fog-edge)"/></mask>
  <g id="map-pine"><ellipse cy="16" rx="25" ry="10" fill="#0b201b" opacity=".5"/><path d="M-4-7h8v30h-8z" fill="#756348"/><path d="M0-60L-20-22h10L-29 2h13L-34 20H34L16 2h13L10-22h10Z" fill="currentColor" stroke="#19382e" stroke-width="2"/><path d="M0-54L-14-25M-9-16L-21 0M-15 8l-10 9" stroke="#6b8970" opacity=".45" stroke-width="2"/></g>
 </defs>
 <rect :width="world.width" :height="world.height" fill="url(#forest-floor)"/>
 <path :d="'M'+(world.width-48)+' 0q-30 180 0 340t0 330t0 330h48V0Z'" fill="#172f3c"/>
 <path :d="'M'+(world.width-43)+' 0q-30 180 0 340t0 330t0 330'" fill="none" stroke="#789995" stroke-width="5" opacity=".4"/>
 <g fill="url(#stone-paving)" stroke="#d1c09a" stroke-width="3" stroke-opacity=".6">
  <rect v-for="(road,i) in world.roads" :key="'r'+i" :x="road[0]" :y="road[1]" :width="road[2]" :height="road[3]"/>
  <rect v-for="(court,i) in world.courts" :key="'c'+i" :x="court[0]" :y="court[1]" :width="court[2]" :height="court[3]"/>
 </g>
 <g aria-hidden="true"><use v-for="(tree,i) in scenery" :key="i" href="#map-pine" :transform="'translate('+tree.x+','+tree.y+') scale('+tree.scale+')'" :color="['#345d46','#42684e','#527359'][tree.tone]"/></g>
 <g v-for="(b,i) in world.buildings" :key="'b'+i" :transform="'translate('+b[0]+','+b[1]+')'" aria-hidden="true">
  <rect x="9" y="14" :width="b[2]" :height="b[3]" fill="#071d19" opacity=".48" rx="4"/>
  <rect x="0" y="0" :width="b[2]" :height="b[3]" fill="url(#house-wall)" stroke="#b8a385" stroke-width="2" rx="3"/>
  <path :d="'M6 '+(b[3]-6)+'H'+(b[2]-6)+'M12 '+(b[3]*.6)+'V'+b[3]+'M'+(b[2]-12)+' '+(b[3]*.6)+'V'+b[3]" stroke="#463f36" stroke-width="5"/>
  <rect x="2" y="3" :width="b[2]-4" :height="b[3]*.65" fill="url(#roof-tiles)" stroke="#94a093" stroke-width="2"/>
  <path :d="'M'+b[2]/2+' 2V'+(b[3]*.65)+'M2 '+b[3]*.65+'H'+(b[2]-2)" stroke="#b2b9a3" stroke-width="3"/>
  <rect :x="b[2]-35" y="-5" width="18" height="26" fill="#6b6455" stroke="#b9a58c" stroke-width="2"/>
  <rect :x="b[2]-38" y="-8" width="24" height="7" fill="#b9a58c"/>
  <rect :x="b[2]/2-12" :y="b[3]-32" width="24" height="31" rx="10" fill="#263b36" stroke="#b6a185" stroke-width="2"/>
  <circle :cx="b[2]/2+6" :cy="b[3]-15" r="2" fill="#d8bd7f"/>
  <g v-for="x in [28,b[2]-43]" :key="x"><circle :cx="x+8" :cy="b[3]-26" r="32" fill="url(#lantern-glow)"/><rect :x="x" :y="b[3]-36" width="18" height="23" rx="2" fill="#f0cb86" stroke="#554e3e" stroke-width="3"/><path :d="'M'+(x+9)+' '+(b[3]-36)+'v23M'+x+' '+(b[3]-25)+'h18'" stroke="#746346" stroke-width="2"/></g>
 </g>
 <g v-for="(p,i) in world.places" :key="p.name" aria-hidden="true">
  <circle :cx="p.x" :cy="p.y" r="38" fill="none" stroke="#e3d1a6" stroke-width="2" opacity=".5"/><circle :cx="p.x" :cy="p.y" r="29" fill="none" stroke="#756d59" opacity=".4"/>
  <path :d="'M'+p.x+' '+(p.y-17)+'l5 12 12 5-12 5-5 12-5-12-12-5 12-5Z'" fill="#6e7562" opacity=".6"/>
  <g :transform="'translate('+(p.x+68)+','+(p.y-38)+')'"><circle cy="-8" r="52" fill="url(#lantern-glow)"/><path d="M0 0v31m-6 0h12" stroke="#39483b" stroke-width="4"/><path d="M-7-17h14l-2 20H-5Z" fill="#efc77e" stroke="#5e5842" stroke-width="3"/><path d="M-10-17h20L0-26Z" fill="#4b5e50"/></g>
  <g :transform="'translate('+p.x+','+(p.y-48)+')'"><rect x="-37" y="-13" width="74" height="26" rx="4" fill="#172e30" stroke="#b4a680"/><text y="5" text-anchor="middle" fill="#f0dfb6" font-size="13" font-weight="600" letter-spacing="2">{{p.name}}</text></g>
 </g>
 <rect :width="world.width" :height="world.height" fill="#07151d" opacity=".8" mask="url(#explore-fog)"/>
 <g v-for="body in world.bodies" :key="'body'+body.id" :transform="'translate('+body.x+','+body.y+')'" class="map-body"><ellipse rx="23" ry="10" fill="#b78c9388"/><path d="M-15 0q15-14 30 0q-15 14-30 0" fill="#c5a3a1"/><text y="31" text-anchor="middle" fill="#ffe2d9" font-size="13">{{name(body.id)}} · 시체</text></g>
 <g v-for="p in rendered.players" :key="p.id" class="map-person" :style="{transform:'translate('+p.x+'px,'+p.y+'px)'}">
  <ellipse cy="13" rx="17" ry="7" fill="#061718" opacity=".5"/>
  <ellipse v-if="p.id===selfId" cy="12" rx="22" ry="10" fill="none" stroke="#f0d294" stroke-width="1.5"/>
  <g :class="{walking:p.moving}" class="map-avatar">
   <path d="M-10 5l-2 11h8l3-10M3 6l2 10h8L10 4" fill="#20373b" stroke="#10272e" stroke-width="2"/>
   <path d="M-11-9Q0-18 11-9L16 10Q0 16-16 10Z" :fill="coat(p.id)" stroke="#203b3c" stroke-width="2"/>
   <path d="M-8-7L0 10 8-7" fill="#f0dbab" opacity=".38"/>
   <circle cy="-16" r="11" :fill="coat(p.id)" stroke="#243d3c" stroke-width="2"/>
   <rect x="-7" y="-20" width="14" height="15" rx="6" fill="#ecd0a7"/>
   <path d="M-8-18q0-13 16-4l1 4-8-3Z" fill="#4e4037"/>
   <path d="M-3-14v2m6-2v2" stroke="#344039" stroke-width="2" stroke-linecap="round"/>
   <path d="M-8-5q8 4 16 0l-1 5H-7Z" fill="#f0d295"/>
  </g>
  <g class="map-name"><rect :x="-labelWidth(p.id)/2" y="23" :width="labelWidth(p.id)" height="21" rx="5" fill="#10272dee" :stroke="p.id===selfId?'#ceb579':'#637b70'" stroke-width="1"/><text y="37" text-anchor="middle" fill="#fff0cf" font-size="11" font-weight="600">{{label(p.id)}}</text></g>
 </g>
 </svg><div class="map-compass" aria-hidden="true"><span>N</span><i></i><small>달빛 마을</small></div></div>
 <details class="map-guide"><summary>마을 안내도 · 방문 장소 {{world.places.length}}곳</summary><svg :viewBox="'0 0 '+world.width+' '+world.height" role="img" aria-label="길과 방문 장소 안내도"><rect :width="world.width" :height="world.height" fill="#172f2a"/><g fill="#9c8965"><rect v-for="(r,i) in world.roads" :key="i" :x="r[0]" :y="r[1]" :width="r[2]" :height="r[3]"/><rect v-for="(r,i) in world.courts" :key="'court'+i" :x="r[0]" :y="r[1]" :width="r[2]" :height="r[3]"/></g><g v-for="p in world.places" :key="p.name"><circle :cx="p.x" :cy="p.y" r="24" fill="#e7c995"/><text :x="p.x" :y="p.y-35" text-anchor="middle" fill="#fff5dd" font-size="32">{{p.name}}</text></g><circle v-if="rendered.self" :cx="rendered.self[0]" :cy="rendered.self[1]" r="20" fill="#8fdbc8"/></svg><p>현재 위치만 표시합니다. 다른 참가자와 시체는 표시하지 않습니다.</p></details>
 <div class="explore-help"><p>지도를 클릭한 뒤 <b>방향키 / WASD</b>로 이동하세요.<br>밝은 길과 장소 안에서만 이동할 수 있습니다. 시야 밖의 사람은 보이지 않습니다. {{world.realtime?'가까운 시체를 신고하면 회의가 열립니다.':'종료 위치가 밤 장소가 됩니다.'}}</p><div class="dpad" aria-label="이동 방향"><button v-for="d in [[0,-1,'위','↑'],[-1,0,'왼쪽','←'],[0,1,'아래','↓'],[1,0,'오른쪽','→']]" :key="d[2]" :disabled="!canMove" :aria-label="d[2]+'로 이동'" @pointerdown.prevent="press($event,d[0],d[1])" @pointerup="stop" @pointercancel="stop" @lostpointercapture="stop" @keydown.enter.prevent="pad=[d[0],d[1]];send()" @keyup.enter="stop" @blur="stop">{{d[3]}}</button></div></div>
 <div v-if="world.realtime" class="field-actions"><p v-if="world.day===1">첫날 평화 시간 · 공격 불가</p><template v-else-if="world.attack_wait!==null"><p>공격 {{world.attack_wait>0?Math.ceil(world.attack_wait)+'초 대기':'준비 완료 · 가까운 대상에게 접근하세요'}}</p><button v-for="id in world.attack_targets" :key="id" :disabled="!canMove || world.attack_wait>0" @click="$emit('act','attack',id)">{{name(id)}} 공격</button></template><button v-for="body in world.bodies" :key="'report'+body.id" :disabled="!canMove || !body.reportable" @click="$emit('act','report',body.id)">{{name(body.id)}} 시체 신고{{body.reportable?'':' · 가까이 이동'}}</button></div>
 </section>`
};
