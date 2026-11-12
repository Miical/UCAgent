"""Read URG text observations without inferring declaration names from elaborated types."""
import re


def read_groups(text):
    """Preserve native type, source, instances and counts, including compressed cross bins."""
    groups=[]
    sections=re.split(r'^Group : (\S+)\s*$',text,flags=re.M)
    for index in range(1,len(sections),2):
        group_type,body=sections[index:index+2]
        source=re.search(r'Source File\(s\) :\s*\n\s*(\S+)',body)
        if not source:
            continue
        details=re.split(r'^Group Instance : (\S+)\s*$',body,flags=re.M)
        rows=[(details[i],details[i+1]) for i in range(1,len(details),2)] or [(group_type,body)]
        declared=re.search(r'^([0-9]+) Instances:',body,re.M)
        if declared and int(declared[1])!=len(rows):
            raise ValueError('URG instance details are incomplete: '+group_type)
        for instance,detail in rows:
            heading=re.split(r'^Summary for (?:Variable|Cross) \S+\s*$',detail,flags=re.M)[0]
            points=[]
            for line in heading.splitlines():
                fields=line.split()
                if len(fields)<8 or not all(v.isdigit() for v in fields[1:4]):
                    continue
                name,total,missing,covered=fields[:4]
                total,missing,covered=map(int,(total,missing,covered))
                if covered+missing!=total:
                    raise ValueError('Inconsistent URG point counts: '+name)
                parts=re.split(r'^Summary for (?:Variable|Cross) (\S+)\s*$',detail,flags=re.M)
                point_body=next((parts[k+1] for k in range(1,len(parts),2) if parts[k]==name),'')
                bins=[]
                # NAME tables carry individual bins; cross combination/element-hole tables
                # are kept as native aggregate totals rather than inventing member counts.
                for table in re.split(r'^NAME\s+COUNT\s+AT LEAST[^\n]*\n',point_body,flags=re.M)[1:]:
                    for item in table.splitlines():
                        match=re.match(r'^(\S+)\s+(\d+)\s+(\d+)(?:\s|$)',item.strip())
                        if not match:
                            break
                        bin_name,hits,goal=match.groups()
                        bins.append(dict(name=bin_name,hits=int(hits),goal=int(goal)))
                reported_bins=bins
                if len(bins)!=total or sum(b['hits']>=b['goal'] for b in bins)!=covered:
                    bins=[]
                points.append(dict(name=name,covered=covered,total=total,bins=bins,reported_bins=reported_bins))
            if not points:
                raise ValueError('URG has no point details: '+group_type)
            groups.append(dict(type=group_type,instance=instance,source=source[1],points=points))
    if not groups:
        raise ValueError('URG has no group observations')
    return groups
