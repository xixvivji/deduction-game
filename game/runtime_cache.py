"""Bounded per-process LRU for immutable event tokenization, never model replies."""
from collections import OrderedDict, Counter
import hashlib
import sys
import threading
import time


def size(value):
    total=sys.getsizeof(value)
    if isinstance(value,dict):total+=sum(size(k)+size(v) for k,v in value.items())
    elif isinstance(value,(tuple,list)):total+=sum(size(v) for v in value)
    return total


class EventTokenCache:
    def __init__(self,max_entries=1024,max_bytes=2*1024*1024,ttl=300,clock=time.monotonic):
        self.max_entries=max_entries;self.max_bytes=max_bytes;self.ttl=ttl;self.clock=clock
        self.items=OrderedDict();self.bytes=0;self.hits=0;self.misses=0;self.evictions=0
        self.lock=threading.Lock()

    def _drop(self):
        _,(_,_,cost)=self.items.popitem(last=False);self.bytes-=cost;self.evictions+=1

    def get(self,namespace,event_id,text,tokenizer):
        key=(*namespace,event_id,hashlib.sha256(text.encode()).digest())
        now=self.clock()
        with self.lock:
            while self.items and now-next(iter(self.items.values()))[0]>=self.ttl:self._drop()
            if key in self.items:
                _,counts,cost=self.items.pop(key);self.items[key]=(now,counts,cost);self.hits+=1
                return Counter(dict(counts)),True
            self.misses+=1
        counts=tuple(Counter(tokenizer(text)).items());cost=size(key)+size(counts)+128
        if cost<=self.max_bytes and self.max_entries>0:
            with self.lock:
                if key in self.items:
                    _,_,old=self.items.pop(key);self.bytes-=old
                while self.items and (len(self.items)>=self.max_entries or self.bytes+cost>self.max_bytes):self._drop()
                self.items[key]=(self.clock(),counts,cost);self.bytes+=cost
        return Counter(dict(counts)),False

    def stats(self):
        with self.lock:return {'entries':len(self.items),'estimated_bytes':self.bytes,'hits':self.hits,'misses':self.misses,'evictions':self.evictions,'max_entries':self.max_entries,'max_estimated_bytes':self.max_bytes,'ttl_seconds':self.ttl}

    def clear(self):
        with self.lock:self.items.clear();self.bytes=0;self.hits=0;self.misses=0;self.evictions=0


TOKEN_CACHE=EventTokenCache()
