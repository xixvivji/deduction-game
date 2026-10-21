const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const motion=vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../frontend/assets/exploration.js'),'utf8')+'\nmapMotion;');
const world=(x,others=[])=>({self_position:[x,500],players:[{id:'me',x,y:500,moving:true},...others]});
test('camera/fog self and avatar share the same frame, even between packets',()=>{
 let packet=motion.packet(world(800),{self:null,players:[]},'me',0,false);
 for(let n=1;n<15;n++){
  const now=n*100,previous=motion.sample(packet,now);
  packet=motion.packet(world(800+n*15),previous,'me',now,false);
  for(const offset of [0,16,33,50,83,100]){
   const frame=motion.sample(packet,now+offset);
   assert.equal(frame.players[0].x,frame.self[0]);
   assert.equal(frame.players[0].x-(frame.self[0]-400),400);
   assert.ok(frame.self[0]>=previous.self[0]);
   assert.ok(frame.self[0]<=800+n*15);
  }
 }
});
test('new packet starts from current interpolation and never jumps backward',()=>{
 let packet=motion.packet(world(800),{self:[780,500],players:[{id:'me',x:780,y:500}]},'me',0,false);
 const at50=motion.sample(packet,50);
 packet=motion.packet(world(820),at50,'me',50,false);
 assert.equal(motion.sample(packet,50).self[0],790);
 assert.equal(motion.sample(packet,100).self[0],805);
 assert.equal(motion.sample(packet,1000).self[0],820);
});
test('out-of-sight players disappear immediately instead of lingering in animation cache',()=>{
 const first=motion.packet(world(800,[{id:'other',x:820,y:500}]),{self:null,players:[]},'me',0,false);
 const next=motion.packet(world(815),motion.sample(first,50),'me',50,false);
 assert.equal(motion.sample(next,50).players.length,1);
});
test('reduced motion snaps and dead observer has no position or avatars',()=>{
 const snap=motion.packet(world(820),{self:[800,500],players:[]},'me',0,true);
 assert.equal(motion.sample(snap,0).self[0],820);
 const dead=motion.packet({self_position:null,players:[]},motion.sample(snap,0),'me',5,false);
 assert.equal(motion.sample(dead,5).self,null);
 assert.equal(motion.sample(dead,5).players.length,0);
});
