"""Locate source definitions structurally; comments carry identity, never line numbers."""
import ast
import hashlib
from pathlib import Path
import re
import tokenize
import io

MARKER = re.compile(r'@flowcanvas\s+id=(bind_[0-9a-f]{32})\b')


def digest(text):
    # Child binding comments and whitespace-only edits don't change semantics.
    text = re.sub(r'(?m)^\s*(?://|#)\s*@flowcanvas[^\n]*\n?', '', text)
    return hashlib.sha256(text.strip().encode()).hexdigest()


def inventory(path):
    path = Path(path)
    text = path.read_text(encoding='utf-8')
    if len(text.encode()) > 2 * 1024 * 1024:
        raise ValueError('源码超过 2 MiB，未扫描')
    rows = []
    if path.suffix == '.py':
        tree = ast.parse(text)
        lines = text.splitlines(keepends=True)
        offsets = [0]
        for line in lines:
            offsets.append(offsets[-1] + len(line))
        def offset(line, column):
            # Python AST columns are UTF-8 byte offsets.
            return offsets[line - 1] + len(lines[line - 1].encode()[:column].decode())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef,
                                     ast.Assign, ast.AnnAssign, ast.Assert)):
                continue
            first = min([node.lineno] + [d.lineno for d in getattr(node, 'decorator_list', [])])
            start = offset(first, node.col_offset)
            end = offset(node.end_lineno, node.end_col_offset)
            kind = type(node).__name__
            name = getattr(node, 'name', '') or ast.get_source_segment(text, node).splitlines()[0][:100]
            rows.append(dict(name=name, kind=kind, start=start, end=end))
        comments = [(offset(tok.start[0], len(lines[tok.start[0]-1][:tok.start[1]].encode())), tok.string)
                    for tok in tokenize.generate_tokens(io.StringIO(text).readline) if tok.type == tokenize.COMMENT]
    else:
        import pyslang as slang
        from pyslang.syntax import SyntaxTree
        from pyslang.parsing import PreprocessorOptions
        options = PreprocessorOptions()
        # Only known registration/logging macros are stubbed. Coverage-generating
        # macros and unknown directives produce errors rather than fabricated nodes.
        options.predefines = ['uvm_component_utils(T)=', 'uvm_object_utils(T)=',
            'uvm_fatal(ID,MSG)=;', 'uvm_error(ID,MSG)=;', 'uvm_warning(ID,MSG)=;',
            'uvm_info(ID,MSG,V)=;', 'uvm_do_with(OBJ,CONSTRAINTS)=;',
            'uvm_do(OBJ)=;', 'uvm_create(OBJ)=;', 'uvm_send(OBJ)=;']
        tree = SyntaxTree.fromFile(str(path), slang.SourceManager(), slang.Bag([options]))
        sm = tree.sourceManager
        errors = [d for d in tree.diagnostics if d.isError()]
        if errors:
            raise ValueError(slang.DiagnosticEngine.reportAll(sm, errors)[:3000])
        raw = text.encode()
        def char_offset(location):
            return len(raw[:location.offset].decode())
        def visit(node):
            kind = str(node.kind).split('.')[-1]
            if kind not in {'CovergroupDeclaration', 'Coverpoint', 'CoverCross', 'CoverageBins', 'BinsSelection', 'ClassDeclaration', 'FunctionDeclaration', 'TaskDeclaration'}:
                return
            if Path(str(sm.getFileName(node.sourceRange.start))).resolve() != path.resolve():
                return  # Includes must be imported explicitly, never silently annotated.
            label = getattr(node, 'label', None)
            start = char_offset(label.sourceRange.start if label else node.sourceRange.start)
            end = char_offset(node.sourceRange.end)
            name = str(label).strip().rstrip(':') if label else str(getattr(node, 'name', '')).strip()
            if not name and hasattr(node, 'prototype'):
                name = str(getattr(node.prototype, 'name', '')).strip()
            if not name:
                name = str(getattr(node, 'expr', '')).strip() or kind
            keyword = str(getattr(node, 'keyword', '')).strip()
            rows.append(dict(name=name, kind=kind, start=start, end=end,
                             base=re.sub(r'^extends\s+', '', str(getattr(node, 'extendsClause', '') or '').strip()).split('#')[0].strip(),
                             excluded=keyword in {'ignore_bins', 'illegal_bins'}, bin_type=keyword))
        tree.root.visit(visit)
        # Only line comments outside strings/block comments can carry IDs.
        tokens = re.finditer(r'"(?:\\.|[^"\\])*"|/\*[\s\S]*?\*/|//[^\n]*', text)
        comments = [(m.start(), m.group()) for m in tokens if m.group().startswith('//')]
    rows.sort(key=lambda x: (x['start'], -x['end']))
    markers = []
    for start, comment in comments:
        match = MARKER.search(comment)
        if match:
            line_end = text.find('\n', start)
            markers.append((start, len(text) if line_end < 0 else line_end + 1, match[1]))
    for row in rows:
        parents = [r for r in rows if r['start'] < row['start'] and r['end'] >= row['end']]
        row['parent_start'] = max((r['start'] for r in parents), default=None)
        row['scope'] = '.'.join(r['name'] for r in sorted(parents, key=lambda x: x['start']))
        row['line'] = text.count('\n', 0, row['start']) + 1
        row['end_line'] = text.count('\n', 0, max(row['start'], row['end'] - 1)) + 1
        row['fingerprint'] = digest(text[row['start']:row['end']])
        row['id'] = next((mid for a, b, mid in reversed(markers)
                          if b <= row['start'] and not text[b:row['start']].strip()), None)
    attached = {r['id'] for r in rows if r['id']}
    return {'nodes': rows, 'markers': [m[2] for m in markers],
            'orphans': [m[2] for m in markers if m[2] not in attached], 'text': text}
