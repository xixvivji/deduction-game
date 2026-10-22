import tempfile
import unittest
from pathlib import Path
from game.server import Hub


class RetentionTests(unittest.TestCase):
    def test_finished_eviction_restore_and_startup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=str(Path(tmp)/'rooms.db');hub=Hub(path)
            room,token=hub.create('test',5);rid=room.data['id']
            room.data['status']='finished';room.data['history']=[{'test':'preserved'}]
            room.last_access=0
            self.assertEqual(hub.evict_finished(),1)
            restored=hub.room(rid)
            self.assertEqual(hub.authenticate(restored,token),'p0')
            self.assertEqual(restored.data['history'],[{'test':'preserved'}])
            hub.db.close();hub=Hub(path)
            self.assertNotIn(rid,hub.rooms)
            self.assertEqual(hub.room(rid).data['status'],'finished');hub.db.close()

    def test_active_connected_busy_and_locked_are_retained(self):
        hub=Hub(':memory:');room,_=hub.create('test',5);room.last_access=0
        self.assertEqual(hub.evict_finished(),0)
        room.data['status']='finished';room.connections=1
        self.assertEqual(hub.evict_finished(),0)
        room.connections=0;room.busy=True
        self.assertEqual(hub.evict_finished(),0)
        room.busy=False
        import asyncio
        asyncio.run(room.lock.acquire())
        self.assertEqual(hub.evict_finished(),0)
        room.lock.release()
        self.assertEqual(hub.evict_finished(),1);hub.db.close()
