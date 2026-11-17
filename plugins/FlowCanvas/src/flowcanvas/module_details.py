"""Resolve module-instance identities and current source from the authoritative RTL model."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from fastapi import APIRouter, HTTPException


def module_record(service, project_id, identity='', instance=''):
    """Resolve a parsed instance, never trusting a browser-supplied source location."""
    project=next((p for p in service.projects.state['recent'] if p['id']==project_id),None)
    if project is None:raise HTTPException(404,'Project not found')
    model=service.rtl_view(project_id)
    if identity and identity!='module_design':raise HTTPException(404,'Design tab not found')
    selected=instance or model['top']
    for node in model['nodes']:
        key='module_design'
        if node['id']!=selected:continue
        source=node.get('source') or {}
        root=Path(project['path']).resolve();path=Path(source.get('file',''))
        path=(path if path.is_absolute() else root/path).resolve()
        if not path.is_relative_to(root) or not path.is_file():raise HTTPException(422,'Module source is unavailable')
        with path.open('rb') as stream:raw=stream.read(2*1024*1024+1)
        if len(raw)>2*1024*1024:raise HTTPException(413,'Module source exceeds 2 MB')
        content=raw.decode('utf-8');start=source.get('start_line',1);end=source.get('end_line',start)
        card=SimpleNamespace(id=key,title='设计总览',name=node['module'],description='RTL module instance '+node['id'],entity_type='module',instance=node['id'],ports=[dict(name=port['name'],direction=port.get('dir','')) for port in node.get('ports',[])],parameters=node.get('parameters',{}))
        child_ids={edge['to'] for edge in model.get('edges',[]) if edge.get('from')==node['id']}
        card.children=[dict(instance=child['id'],key=child['id'].removeprefix(node['id']+'.'),
                            module=child['module'],source=child.get('source'),parameters=child.get('parameters',{}),
                            is_blackbox=child.get('is_blackbox',False))
                       for child in model['nodes'] if child['id'] in child_ids]
        # Explanations are project data, bound to the source revision they describe.
        overview_path=(root/'.flowcanvas/module-overviews.json').resolve()
        card.overview=None
        if overview_path.is_relative_to(root) and overview_path.is_file() and overview_path.stat().st_size<=1024*1024:
            try:
                entries=json.loads(overview_path.read_text(encoding='utf-8'))
                entry=entries.get(str(path.relative_to(root))+'::'+node['module']) if isinstance(entries,dict) else None
                if isinstance(entry,dict) and entry.get('source_sha256')==hashlib.sha256(raw).hexdigest():
                    card.overview=entry
            except (ValueError,OSError):
                pass
        result=dict(file=str(path.relative_to(root)),line=start,end_line=end,content='\n'.join(content.splitlines()[start-1:end]),file_content=content)
        site=node.get('instance_source') or {}
        if site.get('file'):
            site_path=Path(site['file']);site_path=(site_path if site_path.is_absolute() else root/site_path).resolve()
            if site_path.is_relative_to(root) and site_path.is_file():
                with site_path.open('rb') as stream:site_raw=stream.read(2*1024*1024+1)
                if len(site_raw)>2*1024*1024:raise HTTPException(413,'Instantiation source exceeds 2 MB')
                site_start=site.get('start_line',1);site_end=site.get('end_line',site_start)
                site_lines=site_raw.decode('utf-8').splitlines();display_start=max(1,site_start-6);display_end=min(len(site_lines),site_end+6)
                result['instantiation']=dict(file=str(site_path.relative_to(root)),line=site_start,end_line=site_end,
                                             display_line=display_start,display_end_line=display_end,
                                             content='\n'.join(site_lines[display_start-1:display_end]))
        return project,card,result
    raise HTTPException(404,'Module instance not found')


def routes(service):
    """Expose only parsed module instances and their confined source files."""
    router=APIRouter(prefix='/api/chat/projects')
    @router.get('/{project_id}/module')
    def inspect(project_id:str,instance:str):
        """Resolve a hierarchy selection to a stable module tab identity."""
        _,card,source=module_record(service,project_id,instance=instance)
        return dict(module=vars(card),source=source)
    @router.get('/{project_id}/modules/{identity}')
    def read(project_id:str,identity:str):
        """Read current source when restoring an existing module tab."""
        _,card,source=module_record(service,project_id,identity=identity)
        return dict(module=vars(card),source=source)
    return router
