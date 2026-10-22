"""Bounded game-vocabulary expansion, not a general semantic/embedding model.

Groups express search relevance, not factual equivalence. For example, a changed
statement is relevant to a question about lying but does not prove deception.
Patterns compile once and never rewrite the event text shown to the model.
"""
import re

VERSION = 'game-vocabulary-v1'
# Left boundaries avoid matches such as 장소 inside 저장소 or 보호 inside 보호자.
# Korean endings are handled explicitly where a stem would be too ambiguous.
_GROUPS = {
    'statement_consistency': r'거짓말|허위|기만|속였|속이(?:는|려|고)|모순|말(?:을|이)?\s*바[꾸뀌꾼꿨]|진술(?:이|을)?\s*(?:달라|바[꾸뀌꾼꿨])|앞뒤(?:가|는)?\s*(?:안\s*맞|맞지)',
    'whereabouts': r'알리바이|동선|행적|어디|위치|장소|방문|이동|들렀|머물|다녀[왔온]',
    'witness': r'목격|봤|보았|마주[쳤친]|눈(?:으로)?\s*직접\s*[본봤]',
    'protection': r'보호(?:했|하|받|해|된|를|는|의|에|(?=$|[^가-힣a-z0-9]))|치료|살려|살렸|구해\s*줬|지켜\s*줬|공격(?:을)?\s*막',
    'identity': r'정체|진영|역할|직업|조사|검시',
    'expulsion': r'투표|추방|처형|쫓아(?:내|냈)|내보내|내보냈',
    'attack': r'공격|습격|살해|덮쳤|덮친|해쳤|해치',
    'tracking': r'추적|미행|뒤따라|따라갔|따라간',
    'body': r'시체|시신|사체',
}
_PATTERNS = tuple((name, re.compile(r'(?<![가-힣a-z0-9])(?:' + pattern + r')', re.I))
                  for name, pattern in _GROUPS.items())
_PLAYER = re.compile(r'(?<![a-z0-9])p\d+(?![a-z0-9])', re.I)
_WORDS = re.compile(r'[가-힣a-zA-Z0-9]+')


def concepts(text):
    """Return relevance labels only; negated evidence remains eligible for recall."""
    return tuple(name for name, pattern in _PATTERNS if pattern.search(text))


def legacy_terms(text):
    words = _WORDS.findall(text.lower())
    return words + [word[i:i+2] for word in words if len(word) > 2
                    for i in range(len(word)-1)]


def terms(text):
    # Player IDs stay atomic: p10 must not contribute the bigram p1.
    text = _PLAYER.sub(lambda match: ' ' + match.group(0) + ' ', text.lower())
    words = _WORDS.findall(text)
    return words + [word[i:i+2] for word in words
                    if len(word) > 2 and not re.fullmatch(r'p\d+', word)
                    for i in range(len(word)-1)]


def expanded_terms(text):
    # Namespace concept tokens so event prose cannot forge internal labels.
    return terms(text) + ['@concept:' + name for name in concepts(text)]
