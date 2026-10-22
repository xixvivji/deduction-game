"""Server-authoritative, fixed-speed exploration. Client supplies direction, never position."""
import math
from collections import deque

WIDTH, HEIGHT, RADIUS, SIGHT, SPEED = 1600, 1000, 12, 115, 150
MAP_VERSION = 3
PLACES = {'광장': (460, 300), '창고': (820, 180), '숲길': (140, 460),
          '진료소': (460, 820), '시장': (820, 820), '종탑': (1460, 180),
          '여관': (1460, 500), '부두': (1460, 820)}
# The same rectangles are sent to the renderer and used by authoritative movement.
ROADS = [(100,260,1400,80),(100,140,80,740),(780,140,80,740),
         (1420,140,80,740),(100,780,1400,80),(420,260,80,620),(780,140,720,80),
         (950,220,80,100),(580,740,80,100),(1260,540,240,80)]
COURTS = [(x-60,y-60,120,120) for x,y in PLACES.values()] + [(350,230,220,140)]
BUILDINGS = [(220,100,160,120),(900,80,180,140),(540,620,160,120),
             (1100,580,200,140),(1240,380,120,160),(220,560,140,160),(1260,40,140,90)]



# Each room is a separate coordinate space. Exterior clients receive no room layout.
OUTSIDE = 'outside'
DOOR_RANGE = 38
ROOM_FLOOR = (80,60,640,440)
ROOM_ENTRY = (400,460)
INTERIORS = {
    'warehouse': {'name':'창고','outside':(990,244),'building':1,'theme':'warehouse',
                  'furniture':[(140,100,120,70,'crate'),(540,100,120,70,'crate'),(140,320,100,80,'barrel'),(560,320,100,80,'crate')]},
    'clinic': {'name':'진료소','outside':(620,764),'building':2,'theme':'clinic',
               'furniture':[(140,100,90,120,'bed'),(570,100,90,120,'bed'),(140,340,130,50,'cabinet'),(540,340,120,50,'desk')]},
    'inn': {'name':'여관','outside':(1300,564),'building':4,'theme':'inn',
            'furniture':[(140,110,130,70,'table'),(530,110,130,70,'table'),(140,330,120,60,'table'),(540,330,120,60,'counter')]},
    'tower': {'name':'종탑','outside':(1330,154),'building':6,'theme':'tower',
              'furniture':[(340,110,120,100,'bell'),(140,330,100,60,'gears'),(560,330,100,60,'cabinet')]},
}
ROOM_WALLS = [(80,260,200,20),(520,260,200,20)]
ROOM_POINTS = [(400,240),(340,340),(460,340)]


def space_of(state,pid):
    return state.get('spaces',{}).get(pid,OUTSIDE)


def epoch(state,pid):
    return state.get('space_epochs',{}).get(pid,0)


def geometry(space=OUTSIDE):
    if space==OUTSIDE:return WIDTH,HEIGHT,ROADS+COURTS,BUILDINGS
    room=INTERIORS[space]
    return 800,560,[ROOM_FLOOR],ROOM_WALLS+[f[:4] for f in room['furniture']]


def clear(x,y,margin=0,space=OUTSIDE):
    width,height,_,blocks=geometry(space)
    return margin <= x <= width-margin and margin <= y <= height-margin and not any(
        a-margin < x < a+w+margin and b-margin < y < b+h+margin for a,b,w,h in blocks)


def free(x,y,margin=RADIUS,space=OUTSIDE):
    floors=geometry(space)[2]
    return clear(x,y,margin,space) and any(a+margin <= x <= a+w-margin and b+margin <= y <= b+h-margin
                                          for a,b,w,h in floors)


def place(pos,space=OUTSIDE):
    return INTERIORS[space]['name'] if space!=OUTSIDE else min(PLACES,key=lambda name:math.dist(pos,PLACES[name]))


def sees(a,b,space=OUTSIDE):
    distance=math.dist(a,b)
    if distance>SIGHT:return False
    steps=max(1,math.ceil(distance/6))
    return all(clear(a[0]+(b[0]-a[0])*i/steps,a[1]+(b[1]-a[1])*i/steps,0,space) for i in range(steps+1))


def visible(state,viewer,other):
    a=state['positions'].get(viewer);b=state['positions'].get(other)
    return bool(a and b and space_of(state,viewer)==space_of(state,other) and sees(a,b,space_of(state,viewer)))


def walk(pos,dx,dy,dt,space=OUTSIDE):
    length=math.hypot(dx,dy)
    if not length:return list(pos)
    distance=SPEED*min(max(dt,0),.2)
    steps=max(1,math.ceil(distance/6));x,y=pos
    for _ in range(steps):
        nx=x+dx/length*distance/steps
        if free(nx,y,space=space):x=nx
        ny=y+dy/length*distance/steps
        if free(x,ny,space=space):y=ny
    return [x,y]


def route(start,goal,space=OUTSIDE):
    """Four-neighbour grid routing, confined to a single space."""
    cell=lambda p:(int(p[0]//40),int(p[1]//40))
    point=lambda c:(c[0]*40+20,c[1]*40+20)
    def segment(a,b):
        steps=max(1,math.ceil(math.dist(a,b)/5))
        return all(free(a[0]+(b[0]-a[0])*i/steps,a[1]+(b[1]-a[1])*i/steps,space=space) for i in range(steps+1))
    def anchor(pos):
        c=cell(pos)
        options=[(c[0]+dx,c[1]+dy) for dx in range(-2,3) for dy in range(-2,3)]
        return next((n for n in sorted(options,key=lambda n:math.dist(pos,point(n))) if free(*point(n),space=space) and segment(pos,point(n))),None)
    first,last=anchor(start),anchor(goal)
    if first is None or last is None:return []
    parents={first:None};queue=deque([first])
    while queue:
        c=queue.popleft()
        if c==last:break
        for d in [(1,0),(-1,0),(0,1),(0,-1)]:
            n=(c[0]+d[0],c[1]+d[1])
            if n not in parents and free(*point(n),space=space) and segment(point(c),point(n)):
                parents[n]=c;queue.append(n)
    if last not in parents:return []
    path=[list(goal)];c=last
    while c is not None:path.append(list(point(c)));c=parents[c]
    return list(reversed(path))


def door_position(space,door):
    return INTERIORS[door]['outside'] if space==OUTSIDE else ROOM_ENTRY


def can_travel(state,pid,door):
    space=space_of(state,pid)
    if door not in INTERIORS or space not in (OUTSIDE,door):return False
    pos=state['positions'].get(pid);point=door_position(space,door)
    return bool(pos and math.dist(pos,point)<=DOOR_RANGE and sees(pos,point,space))


def travel(state,game,pid,door):
    from .engine import RuleError
    if not state.get('active') or state.get('meeting') or game.phase=='finished' or pid not in game.living() or not can_travel(state,pid,door):
        raise RuleError('살아 있는 참가자만 가까운 출입문을 이용할 수 있습니다')
    inside=space_of(state,pid)!=OUTSIDE
    state.setdefault('spaces',{})[pid]=OUTSIDE if inside else door
    state['positions'][pid]=list(INTERIORS[door]['outside'] if inside else ROOM_ENTRY)
    state.setdefault('space_epochs',{})[pid]=epoch(state,pid)+1
    state['paths'][pid]=[]
    state.setdefault('moving',{})[pid]=False
    # Position changes are private; witnesses are collected through normal sight checks.


def roam(state,game,pid):
    """Plan public map destinations, never consult hidden occupants or bodies."""
    pos=state['positions'][pid];space=space_of(state,pid)
    goals=state.setdefault('goals',{});goal=goals.get(pid)
    if goal and space==goal['space'] and math.dist(pos,goal['point'])<6:
        goals.pop(pid);goal=None
    if goal is None:
        choices=[OUTSIDE,*INTERIORS]
        destination=game.rng.choice([s for s in choices if s!=space]) if space!=OUTSIDE else game.rng.choice(choices)
        goal={'space':destination,'point':list(game.rng.choice(list(PLACES.values()) if destination==OUTSIDE else ROOM_POINTS))}
        goals[pid]=goal
    if space!=goal['space']:
        door=space if space!=OUTSIDE else goal['space']
        if can_travel(state,pid,door):
            travel(state,game,pid,door);pos=state['positions'][pid];space=space_of(state,pid)
        target=goal['point'] if space==goal['space'] else door_position(space,space if space!=OUTSIDE else goal['space'])
    else:target=goal['point']
    state['paths'][pid]=route(pos,target,space)


def set_tactical_path(state,pid,target):
    state.setdefault('goals',{}).pop(pid,None)
    state['paths'][pid]=route(state['positions'][pid],target,space_of(state,pid))


def escape_point(state,pid,threat):
    points=list(PLACES.values()) if space_of(state,pid)==OUTSIDE else [ROOM_ENTRY,*ROOM_POINTS]
    return max(points,key=lambda p:math.dist(p,threat))


def begin(game, duration=30):
    positions={p:[380+(i%5)*40,260+(i//5)*40] for i,p in enumerate(game.living())}
    paths={p:route(pos,PLACES[game.rng.choice(list(PLACES))]) for p,pos in positions.items() if game.players[p].controller=='ai'}
    return {'map_version':MAP_VERSION,'day':game.day,'duration':duration,'elapsed':0.,'positions':positions,'paths':paths,'spaces':{p:OUTSIDE for p in positions},'space_epochs':{},'goals':{},'seen':{},'active':True,'bodies':{},'cooldowns':{},'shields':list(set(t for doctor,t in game.last_protected.items() if doctor in game.living() and t in game.living())),'meeting':False,'moving':{}}


def migrate(state,game):
    if state.get('map_version')==MAP_VERSION:return
    state['spaces']={p:OUTSIDE for p in state['positions']}
    state['space_epochs']={};state['goals']={}
    for pid,pos in state['positions'].items():
        if not free(*pos):
            candidates=[[min(max(pos[0],x+RADIUS),x+w-RADIUS),min(max(pos[1],y+RADIUS),y+h-RADIUS)] for x,y,w,h in ROADS+COURTS]
            state['positions'][pid]=min((p for p in candidates if free(*p)),key=lambda p:math.dist(pos,p))
    state['paths']={pid:route(pos,PLACES[game.rng.choice(list(PLACES))]) for pid,pos in state['positions'].items() if game.players[pid].controller=='ai'}
    state['map_version']=MAP_VERSION


def tick(state, game, dt, intents, now):
    migrate(state,game)
    dt=min(max(dt,0),.2,state['duration']-state['elapsed']);state['elapsed']+=dt
    for pid,pos in state['positions'].items():
        if not game.players[pid].alive:continue
        if game.players[pid].controller=='ai':
            path=state['paths'].get(pid,[])
            while path and math.dist(pos,path[0])<5:path.pop(0)
            if not path:
                roam(state,game,pid);pos=state['positions'][pid];path=state['paths'][pid]
            dx,dy=(path[0][0]-pos[0],path[0][1]-pos[1]) if path else (0,0)
            distance=math.hypot(dx,dy)
            step=min(dt,distance/SPEED) if distance else dt
        else:
            dx,dy,stamp=intents.get(pid,(0,0,0))
            if now-stamp>.35:dx=dy=0
            step=dt
        state['positions'][pid]=walk(pos,dx,dy,step,space_of(state,pid))
        state.setdefault('moving',{})[pid]=math.dist(pos,state['positions'][pid])>.01
    for viewer,a in state['positions'].items():
        if not game.players[viewer].alive:continue
        for other,b in state['positions'].items():
            if other==viewer or not game.players[other].alive or not visible(state,viewer,other):continue
            location=place(b,space_of(state,other));key=f'{viewer}:{other}:{location}:{space_of(state,other)}'
            if key not in state['seen']:
                state['seen'][key]={'viewer':viewer,'player':other,'location':location,'second':round(state['elapsed'],1)}
    if game.realtime:field_bots(state,game)
    return state['elapsed']>=state['duration'] or state.get('meeting',False) or game.phase=='finished'


def finish(state, game):
    if not state['active']:return
    state['active']=False
    game.exploration_day=game.day
    game.exploration_locations={pid:place(pos,space_of(state,pid)) for pid,pos in state['positions'].items()}
    for sight in state['seen'].values():
        game._emit('탐색 목격', {k:v for k,v in sight.items() if k!='viewer'}|{'day':game.day}, sight['viewer'])
    game._emit('회의 소집' if game.realtime else '탐색 종료',{'day':game.day,'reason':'신고' if state.get('meeting') else '시간 종료'})
    if game.realtime:
        game._emit('발견 결과',{'victims':list(state.get('bodies',{})),'day':game.day})
    game.version+=1


def observation(state,game,viewer):
    migrate(state,game)
    alive=game.players[viewer].alive;me=state['positions'].get(viewer)
    space=space_of(state,viewer) if alive else OUTSIDE
    inside=space!=OUTSIDE
    room=INTERIORS.get(space)
    width,height,_,_=geometry(space)
    doors=[{'id':key,'name':value['name'],'x':door_position(space,key)[0],'y':door_position(space,key)[1],
            'available':bool(alive and can_travel(state,viewer,key))} for key,value in INTERIORS.items() if not inside or key==space]
    return {'day':state['day'],'width':width,'height':height,'sight':SIGHT,'space':space,'space_epoch':epoch(state,viewer),
            'interior':{'theme':room['theme'],'floor':ROOM_FLOOR,'walls':ROOM_WALLS,'furniture':room['furniture']} if inside else None,
            'doors':doors,
            'remaining':max(0,round(state['duration']-state['elapsed'],1)),
            'roads':[] if inside else ROADS,'courts':[] if inside else COURTS,'buildings':[] if inside else BUILDINGS,
            'places':[] if inside else [{'name':name,'x':p[0],'y':p[1]} for name,p in PLACES.items()],
            'realtime':game.realtime,
            'attack_wait':max(0,round(state.get('cooldowns',{}).get(viewer,8)-state['elapsed'],1)) if game.players[viewer].role=='wolf' else None,
            'attack_targets':[p for p in game.living() if game.players[p].role!='wolf' and nearby(state,viewer,p,ATTACK_RANGE)] if game.realtime and alive and game.players[viewer].role=='wolf' and game.day>1 else [],
            'bodies':[{'id':p,'x':state['positions'][p][0],'y':state['positions'][p][1],'reportable':nearby(state,viewer,p,REPORT_RANGE)} for p in state.get('bodies',{}) if alive and visible(state,viewer,p)],
            'self_position':list(me) if me and alive else None,
            'location':place(me,space) if me and alive else None,
            'players':[{'id':pid,'x':round(pos[0],1),'y':round(pos[1],1),'moving':state.get('moving',{}).get(pid,False)} for pid,pos in state['positions'].items()
                       if alive and me and game.players[pid].alive and (pid==viewer or visible(state,viewer,pid))]}


ATTACK_RANGE = 42
REPORT_RANGE = 85
COOLDOWN = 15


def nearby(state, actor, target, distance):
    a=state['positions'].get(actor);b=state['positions'].get(target)
    return bool(a and b and math.dist(a,b)<=distance and visible(state,actor,target))


def act(state, game, actor, kind, target):
    from .engine import RuleError
    if not game.realtime or not state['active'] or state.get('meeting') or game.phase=='finished' or actor not in game.living():
        raise RuleError('지금은 현장 행동을 할 수 없습니다')
    if kind=='report':
        if target not in state['bodies'] or not nearby(state,actor,target,REPORT_RANGE):
            raise RuleError('가까이에서 보이는 시체만 신고할 수 있습니다')
        state['meeting']=True
        game._emit('시체 신고',{'actor':actor,'target':target,'location':place(state['positions'][target],space_of(state,target)),'day':game.day})
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
                if viewer!=actor and visible(state,viewer,actor) and visible(state,viewer,target):
                    game._emit('공격 목격',{'actor':actor,'target':target,'location':place(state['positions'][target],space_of(state,target)),'day':game.day,'second':round(state['elapsed'],1)},viewer)
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
        visible_players=[p for p in game.living() if p!=pid and visible(state,pid,p)]
        bodies=[p for p in state['bodies'] if visible(state,pid,p)]
        role=game.players[pid].role
        if bodies and role!='wolf':
            target=min(bodies,key=lambda p:math.dist(me,state['positions'][p]))
            if nearby(state,pid,target,REPORT_RANGE):
                act(state,game,pid,'report',target);return
            set_tactical_path(state,pid,state['positions'][target]);continue
        view=game.observation(pid)
        enemies=[p for p in visible_players if p not in view['self'].get('allies',[])]
        threats={e['data']['actor'] for e in view['events'] if e['type']=='공격 목격'} | {e['data']['target'] for e in view['events'] if e['type']=='조사' and e['data'].get('team')=='wolf'}
        seen_threats=[p for p in visible_players if p in threats]
        if role!='wolf' and seen_threats:
            threat=state['positions'][seen_threats[0]]
            set_tactical_path(state,pid,escape_point(state,pid,threat));continue
        if role=='wolf' and game.day>1 and enemies:
            preferred=game.council.get(pid,{}).get('target')
            target=preferred if preferred in enemies else min(enemies,key=lambda p:math.dist(me,state['positions'][p]))
            if state['elapsed']>=state['cooldowns'].get(pid,8):
                set_tactical_path(state,pid,state['positions'][target])
                try:act(state,game,pid,'attack',target)
                except RuleError:pass
                if game.phase=='finished':return
            else:
                set_tactical_path(state,pid,escape_point(state,pid,state['positions'][target]))
        elif not state['paths'].get(pid):
            roam(state,game,pid)
