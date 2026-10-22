import math
import unittest
from game import exploration as ex
from game.engine import Game


class MapTests(unittest.TestCase):
    def test_grass_and_edges_cannot_be_crossed(self):
        self.assertFalse(ex.free(600,500))
        for direction in [(0,1),(0,-1),(1,1),(-1,-1)]:
            pos=[1100,300]
            for _ in range(80):
                pos=ex.walk(pos,*direction,.2)
                self.assertTrue(ex.free(*pos))
            if direction[0]==0:
                self.assertLessEqual(pos[1],340-ex.RADIUS)
                self.assertGreaterEqual(pos[1],260+ex.RADIUS)

    def test_every_destination_has_walkable_connected_route(self):
        self.assertEqual(len(ex.PLACES),8)
        for name,start in ex.PLACES.items():
            for end in ex.PLACES.values():
                path=ex.route(start,end)
                self.assertTrue(path,name)
                pos=start
                for target in path:
                    for _ in range(20):
                        distance=math.dist(pos,target)
                        if distance<.01:break
                        pos=ex.walk(pos,target[0]-pos[0],target[1]-pos[1],min(.1,distance/ex.SPEED))
                        self.assertTrue(ex.free(*pos))
                    self.assertLess(math.dist(pos,target),.01,(name,target,pos))
                self.assertLess(math.dist(pos,end),.01)

    def test_spawns_sight_and_old_map_recovery(self):
        game=Game('map',{f'p{i}':'ai' for i in range(11)})
        state=ex.begin(game)
        self.assertTrue(all(ex.free(*p) for p in state['positions'].values()))
        self.assertTrue(ex.sees([1000,300],[1110,300]))
        self.assertFalse(ex.sees([1000,300],[1120,300]))
        # Grass is non-walkable, but does not itself act as an opaque wall.
        self.assertTrue(ex.sees([600,500],[610,500]))
        self.assertFalse(ex.sees([210,160],[310,160]))
        state.pop('map_version');state['positions']['p0']=[600,500]
        ex.migrate(state,game)
        self.assertTrue(ex.free(*state['positions']['p0']))
        self.assertEqual(state['map_version'],ex.MAP_VERSION)
        view=ex.observation(state,game,'p0')
        self.assertEqual(view['roads'],ex.ROADS)
        self.assertEqual(view['sight'],115)
        self.assertEqual(set(ex.PLACES)-set(Game.LOCATIONS),set())
