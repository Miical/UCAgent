"""Functional coverage projection: source identities, instance scopes, and bins."""
import hashlib
import json
from pathlib import Path
import re

def source_fingerprint(text):
    """Ignore comments/formatting, preserve string literals and lexical tokens."""
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|/\*[\s\S]*?\*/|//[^\n]*|[A-Za-z_$][\w$]*|\d+|[^\s]', text)
    return hashlib.sha256(json.dumps([t for t in tokens if not t.startswith(('//','/*'))]).encode()).hexdigest()


def parse_groups(text, source):
    """Read this pinned URG text format, retaining per-instance bin counts."""
    result = []
    sections = re.split(r'^Group : (\S+)\s*$', text, flags=re.M)
    for i in range(1, len(sections), 2):
        group_type, body = sections[i:i+2]
        chunks = re.split(r'^Group Instance : (\S+)\s*$', body, flags=re.M)
        for j in range(1, len(chunks), 2):
            instance, detail = chunks[j:j+2]
            points = []
            summary = detail.split('Summary for Variable', 1)[0].split('Summary for Cross', 1)[0]
            for match in re.finditer(r'^([A-Za-z_]\w*)\s+(\d+)\s+(\d+)\s+(\d+)\s+[\d.]+\s+[\d.]+\s+\d+\s+\d+', summary, re.M):
                name, total, missed, covered = match.groups()
                total, missed, covered = map(int, (total, missed, covered))
                if covered + missed != total: raise ValueError('Invalid functional point counts')
                # Names in the table are unique within this covergroup.
                if any(p['name'] == name for p in points): raise ValueError('Duplicate functional point')
                sections_by_point = re.split(r'^Summary for (?:Variable|Cross) (\S+)\s*$', detail, flags=re.M)
                point_body = next((sections_by_point[k+1] for k in range(1,len(sections_by_point),2) if sections_by_point[k]==name), '')
                bins = []
                for table in re.split(r'^NAME\s+COUNT\s+AT LEAST(?:\s+NUMBER)?\s*$', point_body, flags=re.M)[1:]:
                    for line in table.strip().splitlines():
                        row = re.fullmatch(r'(\S+)\s+(\d+)\s+(\d+)(?:\s+(\d+))?\s*', line)
                        if not row: break
                        bin_name, hits, goal, number = row.groups()
                        if number and int(number) != 1: raise ValueError('Compressed functional bins unsupported')
                        bins.append(dict(name=bin_name, hits=int(hits), goal=int(goal)))
                if len({b['name'] for b in bins}) != len(bins): raise ValueError('Duplicate functional bin')
                if bins and (len(bins) != total or sum(b['hits'] >= b['goal'] for b in bins) != covered):
                    raise ValueError('Functional bin details do not match totals: '+name)
                points.append(dict(name=name, covered=covered, total=total, bins=bins))
            if not points: raise ValueError('No functional points parsed for '+instance)
            result.append(dict(type=group_type.replace('$unit::',''), instance=instance,
                               file='verification/src/components/cache_bus_cov.sv',
                               source_fingerprint=source_fingerprint(source), points=points))
    return dict(schema_version=1, groups=result)


def revision(root):
    path=Path(root)/'.flowcanvas/coverage/state.json'
    if not path.exists(): return ''
    s=json.loads(path.read_text())
    return str(s.get('epoch',''))+':'+str(s.get('updated',''))


def project(root, definition, parsed, binding_reports):
    """Match stable source bindings only against the actual compiled source."""
    path=Path(root)/'.flowcanvas/coverage/state.json'
    state=json.loads(path.read_text()) if path.exists() else {}
    functional=(state.get('report') or {}).get('functional', {})
    groups=functional.get('groups', [])
    from collections import Counter
    instances=Counter((g['file'],g['type']) for g in groups)
    fingerprints={file:source_fingerprint(data['text']) for file,data in parsed.items()}
    targets={}
    for target in definition.targets:
        matches=[]; stale=False; conflict=False
        for binding in target.bindings:
            source=parsed.get(binding.file)
            if not source: continue
            node=next((n for n in source['nodes'] if n['id']==binding.id),None)
            if not node: continue
            clean=lambda text: re.sub(r'//[^\n]*|/\*[\s\S]*?\*/', '', text).strip()
            symbol=[clean(part) for part in (node['scope']+'.'+node['name']).split('.')]
            if len(symbol)<2: continue
            kind=node['kind']
            for group in groups:
                if group['file']!=binding.file or group['type'].replace('::','.')!='.'.join(symbol[:2]): continue
                if instances[(group['file'],group['type'])] != 1:
                    conflict=True;continue
                if group['source_fingerprint']!=fingerprints[binding.file] or binding_reports[target.id]['status']!='bound':
                    stale=True;continue
                points=group['points']
                if kind!='CovergroupDeclaration': points=[p for p in points if len(symbol)>2 and p['name']==symbol[2]]
                for point in points:
                    if kind in {'CoverageBins','BinsSelection'}:
                        name=clean(node['name'])
                        bins=[b for b in point['bins'] if b['name']==name or b['name'].startswith(name+'[')]
                        if not bins: continue
                        covered=sum(b['hits']>=b['goal'] for b in bins); total=len(bins)
                    else:
                        bins=point['bins'];covered=point['covered'];total=point['total']
                    source_path=group['instance']
                    if kind!='CovergroupDeclaration': source_path+='.'+point['name']
                    if kind in {'CoverageBins','BinsSelection'}: source_path+='.'+clean(node['name'])
                    matches.append(dict(instance=group['instance'],binding_id=binding.id,source_path=source_path,
                                        point=point['name'],covered=covered,total=total,bins=bins))
        # Deduplicate multiple bindings to the same coverage observation.
        matches=list({(m['instance'],m['point']):m for m in matches}.values())
        targets[target.id]=dict(status='conflict' if conflict else 'stale' if stale else 'available' if matches else 'no_data', observations=[] if conflict else matches)
    return dict(revision=revision(root),runs=len(state.get('runs',[])),targets=targets)
