"""Source bindings defined by stable marker comments and explicit line counts."""
import hashlib
import re
from collections import Counter

MARKER = re.compile(r'^\s*(?://|#)\s*@flowcanvas\s+id=(bind_[0-9a-f]{32})\s*$')


def fingerprint(text):
    """Hash the selected text without nested FlowCanvas marker lines."""
    return hashlib.sha256(''.join(line for line in text.splitlines(keepends=True)
                                   if not MARKER.fullmatch(line.rstrip('\r\n'))).strip().encode()).hexdigest()


def span(text, start, count):
    """Select count physical lines after start, excluding nested marker lines."""
    lines = text.splitlines(keepends=True)
    if not isinstance(start,int) or not isinstance(count,int) or count < 1 or start < 1 or start > len(lines):
        raise ValueError('line and line_count must identify a positive source range')
    seen = 0
    for end in range(start-1,len(lines)):
        if not MARKER.fullmatch(lines[end].rstrip('\r\n')):
            seen += 1
        if seen == count:
            return end+1, ''.join(lines[start-1:end+1])
    raise ValueError('Source range extends beyond end of file')


def snapshot(path, bindings):
    """Locate each stored marker and validate its explicit range without parsing code."""
    text = path.read_text(encoding='utf-8')
    markers = [(n+1,m[1]) for n,line in enumerate(text.splitlines())
               if (m := MARKER.fullmatch(line))]
    counts = Counter(identity for _,identity in markers)
    nodes = []
    for binding in bindings:
        positions = [line for line,identity in markers if identity == binding.id]
        if len(positions) != 1:
            continue
        end, content = span(text,positions[0]+1,binding.line_count)
        nodes.append({'id':binding.id,'kind':'LineRange','name':binding.id,'scope':'',
                      'line':positions[0]+1,'end_line':end,'fingerprint':fingerprint(content)})
    return {'text':text,'markers':list(counts.elements()),'nodes':nodes,'orphans':[]}


def inspect_files(store, files):
    """Return numbered raw source and existing markers for AI range selection."""
    result=[]
    for file in files:
        text=store.file(file).read_text(encoding='utf-8')
        result.append({'file':file,'line_count':len(text.splitlines()),'content':text,
                       'fingerprint':fingerprint(text)})
    return result
