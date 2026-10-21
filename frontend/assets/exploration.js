// Only server-filtered positions enter this component. No hidden player cache.
const ExplorationMap = {
 props:{world:Object,names:Object,selfId:String,running:Boolean,reduced:Boolean},
 emits:['move'],
 data(){return {keys:[],pad:[0,0],timer:null}},
 computed:{canMove(){return this.running&&!!this.world.self_position},direction(){return [Math.sign(Number(this.keys.includes('ArrowRight')||this.keys.includes('d'))-Number(this.keys.includes('ArrowLeft')||this.keys.includes('a'))+this.pad[0]),Math.sign(Number(this.keys.includes('ArrowDown')||this.keys.includes('s'))-Number(this.keys.includes('ArrowUp')||this.keys.includes('w'))+this.pad[1])]}},
 watch:{canMove(value){if(!value)this.stop()}},
 methods:{
  name(id){return this.names?.[id]||'AI '+id.replace('p','')},
  key(event,down){const key=event.key.length===1?event.key.toLowerCase():event.key;if(!['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','w','a','s','d'].includes(key))return;event.preventDefault();if(down&&!this.keys.includes(key))this.keys.push(key);if(!down)this.keys=this.keys.filter(k=>k!==key);this.send()},
  send(){if(this.canMove)this.$emit('move',...this.direction)},
  press(event,dx,dy){if(!this.canMove)return;event.currentTarget.setPointerCapture?.(event.pointerId);this.pad=[dx,dy];this.send()},
  stop(){this.keys=[];this.pad=[0,0];this.$emit('move',0,0)},
  visibility(){if(document.hidden)this.stop()}
 },
 mounted(){this.timer=setInterval(()=>this.send(),100);window.addEventListener('blur',this.stop);document.addEventListener('visibilitychange',this.visibility)},
 beforeUnmount(){clearInterval(this.timer);window.removeEventListener('blur',this.stop);document.removeEventListener('visibilitychange',this.visibility);this.stop()},
 template:`<section class="exploration-panel" :class="{'no-motion':reduced}" aria-label="실시간 마을 탐색">
 <div class="explore-heading"><div><span class="eyebrow">해 질 녘 · 함께 탐색</span><h2>{{world.location||'관전 중'}} <small>{{Math.ceil(world.remaining)}}초 남음</small></h2></div><strong class="explore-status">{{running?'탐색 진행 중':'연결 대기 / 일시 중단'}}</strong></div>
 <div class="explore-canvas" tabindex="0" role="group" aria-label="마을 이동 지도. 클릭 후 방향키 또는 WASD로 이동" @keydown="key($event,true)" @keyup="key($event,false)" @blur="stop">
 <svg viewBox="0 0 960 600" role="img" aria-label="시야 안의 참가자와 마을 지도">
 <defs><pattern id="grass-map" width="40" height="40" patternUnits="userSpaceOnUse"><rect width="40" height="40" fill="#304d42"/><path d="M7 30l3-5 3 5M30 10l2-4" stroke="#54705a" stroke-width="1" opacity=".5"/></pattern><radialGradient id="sight-light"><stop offset="0" stop-color="white"/><stop offset=".8" stop-color="white"/><stop offset="1" stop-color="#555"/></radialGradient><mask id="explore-fog"><rect width="960" height="600" fill="white"/><circle v-if="world.self_position" :cx="world.self_position[0]" :cy="world.self_position[1]" :r="world.sight" fill="black"/></mask></defs>
 <rect width="960" height="600" fill="url(#grass-map)"/><path d="M460 300L820 300V180M460 300L140 300V460M460 300V540" fill="none" stroke="#ab956a" stroke-width="54" opacity=".65"/>
 <g v-for="(b,i) in world.buildings" :key="i"><rect :x="b[0]+8" :y="b[1]+12" :width="b[2]" :height="b[3]" fill="#10272088" rx="8"/><rect :x="b[0]" :y="b[1]" :width="b[2]" :height="b[3]" fill="#78624c" stroke="#b4a383" stroke-width="3" rx="6"/><path :d="'M'+b[0]+','+(b[1]+b[3]/2)+'l'+b[2]/2+','+(-b[3]/2)+' '+b[2]/2+','+b[3]/2" fill="#344a4c" stroke="#b0b19a" stroke-width="3"/><rect :x="b[0]+25" :y="b[1]+b[3]-35" width="20" height="20" fill="#e4b96f"/></g>
 <g v-for="p in world.places" :key="p.name"><circle :cx="p.x" :cy="p.y" r="30" fill="#142b29" stroke="#d2bb8c" stroke-width="2"/><text :x="p.x" :y="p.y+6" text-anchor="middle" fill="#fff0ce" font-size="17">{{p.name}}</text></g>
 <rect width="960" height="600" fill="#07151b" opacity=".7" mask="url(#explore-fog)"/>
 <g v-for="p in world.players" :key="p.id" class="map-person" :style="{transform:'translate('+p.x+'px,'+p.y+'px)'}"><ellipse cx="0" cy="10" rx="16" ry="7" fill="#0006"/><path d="M-12 8L-9-12Q0-23 9-12L12 8Z" :fill="p.id===selfId?'#e3c18a':'#91aaa2'" stroke="#172a30" stroke-width="2"/><circle cy="-13" r="7" fill="#efd1aa"/><circle v-if="p.id===selfId" r="22" fill="none" stroke="#ffe7a3" stroke-dasharray="4 4"/><rect x="-43" y="19" width="86" height="23" rx="5" fill="#11242deb"/><text y="35" text-anchor="middle" fill="#fff2d5" font-size="13">{{name(p.id).slice(0,10)}}{{p.id===selfId?' · 나':''}}</text></g>
 </svg></div>
 <div class="explore-help"><p>지도를 클릭한 뒤 <b>방향키 / WASD</b>로 이동하세요.<br>벽 너머나 멀리 있는 사람은 보이지 않습니다. 종료 위치가 밤 장소가 됩니다.</p><div class="dpad" aria-label="이동 방향"><button v-for="d in [[0,-1,'위','↑'],[-1,0,'왼쪽','←'],[0,1,'아래','↓'],[1,0,'오른쪽','→']]" :key="d[2]" :disabled="!canMove" :aria-label="d[2]+'로 이동'" @pointerdown.prevent="press($event,d[0],d[1])" @pointerup="stop" @pointercancel="stop" @lostpointercapture="stop" @keydown.enter.prevent="pad=[d[0],d[1]];send()" @keyup.enter="stop" @blur="stop">{{d[3]}}</button></div></div>
 </section>`
};
