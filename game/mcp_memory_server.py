"""Private stdio MCP: signed context registration, capability-scoped search, rules resource."""
import hmac
import os
import secrets
import time
from mcp.server.fastmcp import FastMCP
from .memory_backend import RULES, scoped, signature
from .memory import retrieve

mcp=FastMCP('deduction-memory',log_level='WARNING')
contexts={}


@mcp.resource('game://rules/evidence')
def evidence_rules()->str:
    return RULES


@mcp.tool()
def register_context(view:dict,proof:str)->dict:
    """Trusted application only: register a signed participant observation, never a full game."""
    secret=os.environ.get('GAME_MEMORY_SECRET')
    if not secret or not hmac.compare_digest(signature(view,secret),proof):raise ValueError('Invalid context signature')
    now=time.monotonic()
    for token in list(contexts):
        if now-contexts[token][0]>300:contexts.pop(token)
    if len(contexts)>=64:raise ValueError('Context capacity exceeded')
    token=secrets.token_urlsafe(32);contexts[token]=(now,scoped(view))
    return {'capability':token}


@mcp.tool()
def search_memory(capability:str,query:str='',expanded:bool=False)->dict:
    """Search only this capability's participant snapshot. Caller cannot select another actor."""
    item=contexts.get(capability)
    if not item or time.monotonic()-item[0]>300:raise ValueError('Expired or invalid context')
    return retrieve(item[1],query or None,top_k=16 if expanded else 8)


@mcp.tool()
def release_context(capability:str)->dict:
    """Discard the short-lived snapshot after a decision search."""
    contexts.pop(capability,None)
    return {'released':True}


if __name__=='__main__':mcp.run(transport='stdio')
