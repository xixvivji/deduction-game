"""Server-authoritative, fixed-speed exploration. Client supplies direction, never position."""
import math
from collections import deque

WIDTH, HEIGHT, RADIUS, SIGHT, SPEED = 960, 600, 12, 180, 150
BUILDINGS = [(160, 100, 160, 140), (600, 80, 160, 160), (400, 380, 160, 100)]
PLACES = {'광장': (460, 300), '창고': (820, 180), '숲길': (140, 460)}


def free(x, y, margin=RADIUS):
    return margin <= x <= WIDTH-margin and margin <= y <= HEIGHT-margin and not any(
        a-margin < x < a+w+margin and b-margin < y < b+h+margin for a,b,w,h in BUILDINGS)


def place(pos):
    return min(PLACES, key=lambda name: math.dist(pos, PLACES[name]))


def sees(a, b):
    distance=math.dist(a,b)
    if distance > SIGHT: return False
    steps=max(1, math.ceil(distance/6))
    return all(free(a[0]+(b[0]-a[0])*i/steps, a[1]+(b[1]-a[1])*i/steps, 0) for i in range(steps+1))


def walk(pos, dx, dy, dt):
    length=math.hypot(dx,dy)
    if not length: return list(pos)
    distance=SPEED*min(max(dt,0),.2)
    steps=max(1,math.ceil(distance/6));x,y=pos
    for _ in range(steps):
        nx=x+dx/length*distance/steps
        if free(nx,y):x=nx
        ny=y+dy/length*distance/steps
        if free(x,ny):y=ny
    return [x,y]


def route(start, goal):
    """Four-neighbour grid routing with collision-safe cell centres."""
    cell=lambda p:(int(p[0]//40),int(p[1]//40))
    point=lambda c:(c[0]*40+20,c[1]*40+20)
    first,last=cell(start),cell(goal);parents={first:None};queue=deque([first])
    while queue:
        c=queue.popleft()
        if c==last:break
        for d in [(1,0),(-1,0),(0,1),(0,-1)]:
            n=(c[0]+d[0],c[1]+d[1])
            if n not in parents and free(*point(n)):
                parents[n]=c;queue.append(n)
    if last not in parents:return []
    path=[];c=last
    while c!=first:path.append(list(point(c)));c=parents[c]
    return list(reversed(path))


def begin(game, duration=30):
    positions={p:[380+(i%5)*40,260+(i//5)*40] for i,p in enumerate(game.living())}
    paths={p:route(pos,PLACES[game.rng.choice(list(PLACES))]) for p,pos in positions.items() if game.players[p].controller=='ai'}
    return {'day':game.day,'duration':duration,'elapsed':0.,'positions':positions,'paths':paths,'seen':{},'active':True,'bodies':{},'cooldowns':{},'shields':list(set(t for doctor,t in game.last_protected.items() if doctor in game.living() and t in game.living())),'meeting':False,'moving':{}}


def tick(state, game, dt, intents, now):
    dt=min(max(dt,0),.2,state['duration']-state['elapsed']);state['elapsed']+=dt
    for pid,pos in state['positions'].items():
        if not game.players[pid].alive:continue
        if game.players[pid].controller=='ai':
            path=state['paths'].get(pid,[])
            while path and math.dist(pos,path[0])<5:path.pop(0)
            dx,dy=(path[0][0]-pos[0],path[0][1]-pos[1]) if path else (0,0)
            distance=math.hypot(dx,dy)
            step=min(dt,distance/SPEED) if distance else dt
        else:
            dx,dy,stamp=intents.get(pid,(0,0,0))
            if now-stamp>.35:dx=dy=0
            step=dt
        state['positions'][pid]=walk(pos,dx,dy,step)
        state.setdefault('moving',{})[pid]=math.dist(pos,state['positions'][pid])>.01
    for viewer,a in state['positions'].items():
        if not game.players[viewer].alive:continue
        for other,b in state['positions'].items():
            if other==viewer or not game.players[other].alive or not sees(a,b):continue
            location=place(b);key=f'{viewer}:{other}:{location}'
            if key not in state['seen']:
                state['seen'][key]={'viewer':viewer,'player':other,'location':location,'second':round(state['elapsed'],1)}
    if game.realtime:field_bots(state,game)
    return state['elapsed']>=state['duration'] or state.get('meeting',False) or game.phase=='finished'


def finish(state, game):
    if not state['active']:return
    state['active']=False
    game.exploration_day=game.day
    game.exploration_locations={pid:place(pos) for pid,pos in state['positions'].items()}
    for sight in state['seen'].values():
        game._emit('탐색 목격', {k:v for k,v in sight.items() if k!='viewer'}|{'day':game.day}, sight['viewer'])
    game._emit('회의 소집' if game.realtime else '탐색 종료',{'day':game.day,'reason':'신고' if state.get('meeting') else '시간 종료'})
    if game.realtime:
        game._emit('발견 결과',{'victims':list(state.get('bodies',{})),'day':game.day})
    game.version+=1


def observation(state, game, viewer):
    alive=game.players[viewer].alive;me=state['positions'].get(viewer)
    return {'day':state['day'],'width':WIDTH,'height':HEIGHT,'sight':SIGHT,
            'remaining':max(0,round(state['duration']-state['elapsed'],1)),
            'buildings':BUILDINGS,'places':[{'name':name,'x':p[0],'y':p[1]} for name,p in PLACES.items()],
            'realtime':game.realtime,
            'attack_wait': max(0,round(state.get('cooldowns',{}).get(viewer,8)-state['elapsed'],1)) if game.players[viewer].role=='wolf' else None,
            'attack_targets':[p for p in game.living() if game.players[p].role!='wolf' and nearby(state,viewer,p,ATTACK_RANGE)] if game.realtime and alive and game.players[viewer].role=='wolf' and game.day>1 else [],
            'bodies':[{'id':p,'x':state['positions'][p][0],'y':state['positions'][p][1],'reportable':nearby(state,viewer,p,REPORT_RANGE)} for p in state.get('bodies',{}) if alive and me and sees(me,state['positions'][p])],
            'self_position':list(me) if me and alive else None,
            'location':place(me) if me and alive else None,
            'players':[{'id':pid,'x':round(pos[0],1),'y':round(pos[1],1),'moving':state.get('moving',{}).get(pid,False)} for pid,pos in state['positions'].items()
                       if alive and me and game.players[pid].alive and (pid==viewer or sees(me,pos))]}


ATTACK_RANGE = 42
REPORT_RANGE = 85
COOLDOWN = 15


def nearby(state, actor, target, distance):
    a=state['positions'].get(actor);b=state['positions'].get(target)
    return bool(a and b and math.dist(a,b)<=distance and sees(a,b))


def act(state, game, actor, kind, target):
    from .engine import RuleError
    if not game.realtime or not state['active'] or state.get('meeting') or game.phase=='finished' or actor not in game.living():
        raise RuleError('지금은 현장 행동을 할 수 없습니다')
    if kind=='report':
        if target not in state['bodies'] or not nearby(state,actor,target,REPORT_RANGE):
            raise RuleError('가까이에서 보이는 시체만 신고할 수 있습니다')
        state['meeting']=True
        game._emit('시체 신고',{'actor':actor,'target':target,'location':place(state['positions'][target]),'day':game.day})
    elif kind=='attack':
        if game.players[actor].role!='wolf' or game.day==1 or target not in game.living() or game.players[target].role=='wolf':
            raise RuleError('공격할 수 없는 대상입니다')
        if state['elapsed']<state['cooldowns'].get(actor,8) or not nearby(state,actor,target,ATTACK_RANGE):
            raise RuleError('공격 대기 시간 또는 거리 조건을 확인하세요')
        state['cooldowns'][actor]=state['elapsed']+COOLDOWN
        if target in state['shields']:
            state['shields'].remove(target)
            game._emit('보호 발동',{'target':target,'day':game.day},[actor,target])
        else:
            for viewer in game.living():
                if viewer!=actor and sees(state['positions'][viewer],state['positions'][actor]) and sees(state['positions'][viewer],state['positions'][target]):
                    game._emit('공격 목격',{'actor':actor,'target':target,'location':place(state['positions'][target]),'day':game.day,'second':round(state['elapsed'],1)},viewer)
            game.players[target].alive=False
            state['bodies'][target]={'second':round(state['elapsed'],1)}
            game._emit('현장 공격',{'actor':actor,'target':target,'day':game.day},actor)
            game._check_winner()
    else: raise RuleError('지원하지 않는 현장 행동입니다')
    game.version+=1


def field_bots(state,game):
    """One-second tactical policy using only visible positions and personal council/memory.

    LLM council supplies an optional target; movement and immediate actions are deterministic.
    """
    from .engine import RuleError
    if state['elapsed']<state.get('next_think',0):return
    state['next_think']=state['elapsed']+1
    for pid in list(game.living()):
        if game.players[pid].controller!='ai':continue
        me=state['positions'][pid]
        visible=[p for p in game.living() if p!=pid and sees(me,state['positions'][p])]
        bodies=[p for p in state['bodies'] if sees(me,state['positions'][p])]
        role=game.players[pid].role
        if bodies and role!='wolf':
            target=min(bodies,key=lambda p:math.dist(me,state['positions'][p]))
            if nearby(state,pid,target,REPORT_RANGE):
                act(state,game,pid,'report',target);return
            state['paths'][pid]=route(me,state['positions'][target]);continue
        view=game.observation(pid)
        enemies=[p for p in visible if p not in view['self'].get('allies',[])]
        threats={e['data']['actor'] for e in view['events'] if e['type']=='공격 목격'} | {e['data']['target'] for e in view['events'] if e['type']=='조사' and e['data'].get('team')=='wolf'}
        seen_threats=[p for p in visible if p in threats]
        if role!='wolf' and seen_threats:
            threat=state['positions'][seen_threats[0]]
            state['paths'][pid]=route(me,PLACES[max(PLACES,key=lambda n:math.dist(PLACES[n],threat))]);continue
        if role=='wolf' and game.day>1 and enemies:
            preferred=game.council.get(pid,{}).get('target')
            target=preferred if preferred in enemies else min(enemies,key=lambda p:math.dist(me,state['positions'][p]))
            if state['elapsed']>=state['cooldowns'].get(pid,8):
                state['paths'][pid]=route(me,state['positions'][target])
                try:act(state,game,pid,'attack',target)
                except RuleError:pass
                if game.phase=='finished':return
            else:
                state['paths'][pid]=route(me,PLACES[max(PLACES,key=lambda n:math.dist(PLACES[n],state['positions'][target]))])
        elif not state['paths'].get(pid):
            state['paths'][pid]=route(me,PLACES[game.rng.choice(list(PLACES))])
