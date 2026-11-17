"""Conservative formal-test outcomes from process state and explicit framework log evidence."""
import re
from .smoke_runs import startup_error


def test_result(run, text):
    """Return only running/passed/failed, leaving unproven success and smoke runs unmarked."""
    if run.get('mode') in {'startup_smoke','coverage_smoke'}:
        return dict(status=None,reason='Smoke runs do not determine formal test results')
    if run.get('status') in {'starting','running'}:
        return dict(status='running',reason='Test is executing')
    text=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',text)
    error=startup_error(text)
    failure=re.search(r'^.*(?:\bassertion\b.*\bfailed\b|\bTEST(?:\s+CASE)?\s+FAILED\b|\d+ failed(?:,| in |\s*=)).*$',text,re.M|re.I)
    if error or failure:
        return dict(status='failed',reason=error or failure[0][:500])
    if run.get('status') in {'failed','interrupted'} or run.get('exit_code') not in (None,0):
        return dict(status='failed',reason=run.get('error') or 'Execution '+str(run.get('status'))+'; exit code '+str(run.get('exit_code')))
    if run.get('status')!='completed' or run.get('exit_code')!=0:
        return dict(status=None,reason='No completed execution evidence')
    errors=re.findall(r'^\s*UVM_ERROR\s*:\s*(\d+)\s*$',text,re.M)
    fatals=re.findall(r'^\s*UVM_FATAL\s*:\s*(\d+)\s*$',text,re.M)
    if errors and fatals and all(int(value)==0 for value in errors+fatals):
        return dict(status='passed',reason='Exit code 0; UVM_ERROR=0 and UVM_FATAL=0')
    passed=re.search(r'^.*\bTEST(?:\s+CASE)?\s+PASSED\b.*$|^\s*(?:=+\s*)?\d+ passed(?:, [\w ,]+)? in [^\n]+$',text,re.M|re.I)
    if passed:
        return dict(status='passed',reason='Exit code 0; '+passed[0].strip()[:450])
    return dict(status=None,reason='Exit code 0 without an explicit supported test-pass result')
