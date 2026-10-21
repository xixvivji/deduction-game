"""Cheap guard for common Korean game assertions, not a general prose fact checker.

A detected assertion must go through a typed claim. Quoted/reported assertions
are conservatively repaired too: someone saying a fact does not establish it.
Questions, negations and uncertainty without an affirmative predicate remain free text.
"""
import re

ASSERTIONS = tuple(re.compile(pattern) for pattern in (
    r'(?:늑대|시민|조사자|의사|추적자|검시관)(?:\s*진영)?(?:입니다|예요|이다|다(?=[.!?。！？\s]|$)|였다|였습|가\s*맞|로\s*확인)',
    r'(?:공격|살해|살인)(?:을\s*)?(?:했습니다|했어요|했다|했어(?![요?？])|하였습니다|하는\s*(?:걸|것을)\s*(?:봤|보았)|한\s*(?:걸|것을)\s*(?:봤|보았))',
    r'(?:죽였습니다|죽였어요|죽였다|살해범입니다|범인입니다)',
    r'(?:광장|창고|숲길|진료소|시장|종탑|여관|부두|묘지|도서관|성당|농장|수원지|대장간)(?:에|에서)\s*(?:있습니다|있었습니다|있었어요|있었다|봤습니다|보았습니다|목격했습니다)',
    r'(?:방문|조사|검시|목격)(?:을\s*)?(?:했습니다|했어요|했다|하였습니다)',
))


def contains_untyped_fact(text):
    return any(pattern.search(text) for pattern in ASSERTIONS)
