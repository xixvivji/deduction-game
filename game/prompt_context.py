"""Lossless reference deduplication for model input; full audit remains server-side."""
import copy


def compact(view):
    result=copy.deepcopy(view)
    result.pop('memory',None)  # operational diagnostics, not game evidence
    result.pop('belief_checkpoint',None)
    ctx=result.get('discussion_context')
    if ctx:
        # These events are already pinned into events by discussion.attach_context.
        for key in ('own_recent','public_recent'):
            ctx[key]=[e['sequence'] if isinstance(e,dict) else e for e in ctx[key]]
    ledger=result.get('belief_memory')
    if ledger:
        ledger.pop('_checkpoint',None)
        # Changes/history repeat identical entries; preserve the complete history by reference.
        history=ledger.get('history',[])
        changes=ledger.get('changes',[])
        if changes and len(changes)<=len(history) and history[-len(changes):]==changes:
            ledger['changes']={'history_indices':list(range(len(history)-len(changes),len(history)))}
    return result
