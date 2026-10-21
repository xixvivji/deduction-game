"""Bounded Korean clause guard. This is not a general semantic truth verifier.

Detect common game predicates, then allow only local non-assertive scopes or
server-known self disclosure / faithful cited public speech. Never exempt an
entire response because it contains one question, condition, or valid quote.
Regexes are compiled once; each inspection keeps its own participant context.
"""
import re
import unicodedata

VERSION = 'clause-guard-v2'
ROLE_LABELS = {'citizen':'시민', 'seer':'조사자', 'doctor':'의사',
               'tracker':'추적자', 'coroner':'검시관', 'wolf':'늑대'}
ROLE = re.compile(
    r'(?P<label>늑대|시민|조사자|의사|추적자|검시관|범인|살해범)(?P<team>\s*진영)?'
    r'(?=\s*(?:입니다|예요|이에요|이다|다(?=[\s.!?。！？,]|$)|야|이야|이고|이며|이었|이라|였|인|일|라면|라고|라는|가|은|는|맞|아니|같))')
ACTION = re.compile(
    r'(?:공격|습격|살해|처치|살인|방문|조사|검시|목격|추적|보호|치료)(?:을\s*|를\s*)?'
    r'(?:했|하였|한|하는|하고|하지\s*않|해(?:서|요|버)|당했|당한)'
    r'|죽(?:였|인|이는|이지\s*않)|(?:찌른|찔렀)|해쳤')
LOCATION = re.compile(
    r'(?:광장|창고|숲길|진료소|시장|종탑|여관|부두)(?:에|에서)\s*'
    r'(?:있|없|갔|왔|들렀|머물|보였)')
# Clause boundaries prevent an exemption in one clause hiding another assertion.
BOUNDARY = re.compile(r'[.!?。！？;,\n]+|\s+(?:그리고|하지만|그러나|그런데|다만|그러므로|따라서)\s+')
CONDITION = re.compile(r'다면|라면|으면|인\s*경우|한\s*경우|가정(?:하면|하자면)')
UNCERTAINTY = re.compile(
    r'(?:는지|인지)(?:는|도|를)?\s*.{0,16}(?:모르|알\s*수\s*없|확인.{0,6}(?:없|못)|불확실)'
    r'|일\s*수도|일지도|인\s*것\s*같|했다는\s*(?:근거|증거|기록).{0,10}(?:없|부족)')
QUESTION = re.compile(r'(?:나요|습니까|인가요|일까|을까|까요|냐|니)\s*$')
REQUEST = re.compile(r'(?:알려|보여|설명해|확인해).{0,12}(?:주세요|줄래|주실래|수\s*있나요)\s*$')
DECLARATIVE = re.compile(r'습니다|입니다|예요|이에요|(?:이다|했다|했어|봤어)(?=\s|$)')
AFFIRMATIVE_ROLE = re.compile(r'^\s*(?:입니다|예요|이에요|이다(?=[\s.!?,]|$)|다(?=[\s.!?,]|$)|이야|야(?=[\s.!?,]|$)|이고|이며)')
QUOTE = re.compile(r'["“](?P<quote>[^"”]+)["”]')
REPORT = re.compile(r'^\s*(?:라고|라며)\s*(?:(?P<actor>p\d+)(?:가|이|는|은)\s*)?'
                    r'(?:말했|주장했|발언했|설명했)(?:습니다|어요|다|어)')


def normalize(text):
    return re.sub('[\u200b-\u200d\ufeff]', '', unicodedata.normalize('NFKC', text))


def _public_quotes(view, text, ids):
    """The quote must be the entire cited utterance, with the actual speaker.

    Quoting only the first half of a denial is not a faithful report. A quote
    validates the reported words, not the truth of the quoted accusation.
    """
    sources = [e for e in view.get('events', []) if e['sequence'] in ids
               and e.get('recipient') is None and e['type'] in ('발언','답변')]
    spans = []
    for quote in QUOTE.finditer(text):
        report = REPORT.match(text[quote.end():])
        if not report:continue
        actor = report['actor']
        if actor is None:
            prefix = re.search(r'(p\d+)(?:가|이|는|은)\s*$', text[:quote.start()])
            actor = prefix[1] if prefix else None
        words = ' '.join(quote['quote'].split())
        if actor and any(e['data'].get('actor') == actor and
                         ' '.join(normalize(e['data'].get('text','')).split()) == words
                         for e in sources):
            spans.append((quote.start(), quote.end()))
    return spans


def _self_disclosure(view, segment, match):
    if not AFFIRMATIVE_ROLE.match(segment[match.end():]):return False
    me = view.get('self', {})
    pid = re.escape(me.get('id', '<unknown>'))
    prefix = segment[:match.start()]
    # Require the subject immediately before THIS predicate. 'p0' is not 'p01'.
    subject = re.search(r'(?:^|\s)(?:저는|나는|제가|내가|(?:제|내)\s*(?:직업|역할|정체)은|'
                        + pid + r'(?!\d)(?:은|는|이|가))\s*$', prefix)
    if not subject:return False
    if match['team']:
        return match['label'] == '시민' and me.get('role') in ('citizen','seer','doctor','tracker','coroner')
    return match['label'] == ROLE_LABELS.get(me.get('role'))


def _non_assertion(local, terminal):
    declaration = DECLARATIVE.search(local)
    end = declaration.start() if declaration else len(local)
    # Only a condition/uncertainty belonging to the current predicate is exempt.
    condition = CONDITION.search(local[:48])
    if condition and condition.start() < end:return 'conditional'
    uncertainty = UNCERTAINTY.search(local)
    if uncertainty and uncertainty.start() < end:return 'uncertainty'
    question = QUESTION.search(local) or REQUEST.search(local)
    if question and question.start() < end:return 'question'
    # Colloquial intonation questions ('봤어?') have no formal question ending.
    if ('?' in terminal or '？' in terminal) and re.search(r'(?:어|요|야|까|죠|지)\s*$', local):
        if not re.search(r'(?:입니다|습니다).+\S', local):return 'question'
    return None


def inspect_opinion(view, text, ids=()):
    text = normalize(text)
    quoted = _public_quotes(view, text, set(ids))
    exempted, blocked = [], []
    start = 0
    boundaries = [(m.start(), m.end(), m.group()) for m in BOUNDARY.finditer(text)]
    boundaries.append((len(text), len(text), ''))
    for end, following, terminal in boundaries:
        segment = text[start:end]
        candidates = sorted([(m.start(), kind, m) for kind, pattern in
                             [('role',ROLE),('action',ACTION),('location',LOCATION)]
                             for m in pattern.finditer(segment)], key=lambda row:row[0])
        for i, (offset, kind, match) in enumerate(candidates):
            absolute = start + offset
            stop = candidates[i+1][0] if i+1<len(candidates) else len(segment)
            local = segment[offset:stop].strip()
            reason = None
            if any(a <= absolute and start+match.end() <= b for a,b in quoted):
                reason = 'cited_public_quote'
            if reason is None:
                reason = _non_assertion(local, terminal if i+1==len(candidates) else '')
            if reason is None and kind == 'role' and _self_disclosure(view, segment, match):
                reason = 'known_self_role'
            item = {'kind':kind, 'offset':absolute, 'reason':reason or 'untyped_assertion'}
            (exempted if reason else blocked).append(item)
        start = following
    return {'ok':not blocked, 'version':VERSION, 'exempted':exempted, 'blocked':blocked}


def contains_untyped_fact(text):
    """Compatibility helper without authority to exempt self roles or quotes."""
    return not inspect_opinion({}, text)['ok']
