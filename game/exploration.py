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
    return {'day':game.day,'duration':duration,'elapsed':0.,'positions':positions,'paths':paths,'seen':{},'active':True}


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
    for viewer,a in state['positions'].items():
        if not game.players[viewer].alive:continue
        for other,b in state['positions'].items():
            if other==viewer or not game.players[other].alive or not sees(a,b):continue
            location=place(b);key=f'{viewer}:{other}:{location}'
            if key not in state['seen']:
                state['seen'][key]={'viewer':viewer,'player':other,'location':location,'second':round(state['elapsed'],1)}
    return state['elapsed']>=state['duration']


def finish(state, game):
    state['active']=False
    game.exploration_day=game.day
    game.exploration_locations={pid:place(pos) for pid,pos in state['positions'].items()}
    for sight in state['seen'].values():
        game._emit('탐색 목격', {k:v for k,v in sight.items() if k!='viewer'}|{'day':game.day}, sight['viewer'])
    game._emit('탐색 종료',{'day':game.day})
    game.version+=1


def observation(state, game, viewer):
    alive=game.players[viewer].alive;me=state['positions'].get(viewer)
    return {'day':state['day'],'width':WIDTH,'height':HEIGHT,'sight':SIGHT,
            'remaining':max(0,round(state['duration']-state['elapsed'],1)),
            'buildings':BUILDINGS,'places':[{'name':name,'x':p[0],'y':p[1]} for name,p in PLACES.items()],
            'self_position':list(me) if me and alive else None,
            'location':place(me) if me and alive else None,
            'players':[{'id':pid,'x':round(pos[0],1),'y':round(pos[1],1)} for pid,pos in state['positions'].items()
                       if alive and me and game.players[pid].alive and (pid==viewer or sees(me,pos))]}
