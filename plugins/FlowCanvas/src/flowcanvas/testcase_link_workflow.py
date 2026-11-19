"""Plan and verify changes to existing Bug links and test case expectations."""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from ucagent.checkers.base import Checker

from .bugs import Bugs
from .targets import atomic
from .testcases import Cases


class LinkReview(BaseModel):
    """Desired associations for a reviewed Bug."""

    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    bug_id: str
    case_ids: list[str]
    reason: str = Field(min_length=1)


class ExpectedChange(BaseModel):
    """One concrete case assertion change with an independent behavioral basis."""

    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    bug_id: str
    case_id: str
    file: str
    expected: str = Field(min_length=1, max_length=1000)
    basis: str = Field(min_length=1)


REVIEWS = TypeAdapter(list[LinkReview])
EXPECTATIONS = TypeAdapter(list[ExpectedChange])


def bug_context(bug):
    """Capture the review and scenario apart from mutable card links."""
    return bug.model_dump(mode='json', include={
        'status', 'review_note', 'review_expected', 'evidence', 'name', 'description', 'condition',
        'expected', 'actual', 'source_refs',
    })


def expectations(steps):
    for step in steps:
        yield step.expected
        yield from expectations(step.children)


def assertion_bindings(report, file):
    return {binding['id']: binding['fingerprint'] for binding in report['bindings']
            if binding['file'] == file and binding['role'] in {'definition', 'check'}}


class TestcaseLinkChecker(Checker):
    """Accept a source-backed change plan, then verify the persisted result."""

    accepted_stage_args = ('reviews', 'expectations')

    def __init__(self, mode, **kwargs):
        super().__init__()
        if mode not in {'plan', 'verify'}:
            raise ValueError('Unknown testcase link stage')
        self.mode = mode

    def get_template_data(self):
        path = Path(self.workspace).parent / 'testcase-link-plan.json'
        return {'TESTCASE_LINK_PLAN': path.read_text() if path.is_file() else '{}'}

    def do_check(self, reviews=None, expectations=None, is_complete=False, **kwargs):
        """Validate the proposed changes or check their published results."""
        try:
            run = Path(self.workspace).parent
            request = json.loads((run / 'request.json').read_text())
            bugs = Bugs(request['project_root'])
            cases = Cases(request['project_root'])
            path = run / 'testcase-link-plan.json'
            if self.mode == 'verify':
                return True, self.verify(path, bugs, cases)
            if not isinstance(reviews, list) or not isinstance(expectations, list):
                raise ValueError('Submit reviews and expectations as arrays, including empty arrays')
            plan = self.plan(bugs, cases, REVIEWS.validate_python(reviews),
                             EXPECTATIONS.validate_python(expectations))
            if is_complete:
                atomic(path, plan)
            return True, {'reviewed_bugs': len(plan['reviews']), 'expectation_changes': len(plan['expectations'])}
        except (OSError, KeyError, ValueError) as exc:
            return False, {'error': str(exc)}

    def plan(self, bugs, cases, reviews, changes):
        ready = {row.id: row for row in bugs.read().targets if row.lifecycle == 'ready' and not row.example}
        by_bug = {row.bug_id: row for row in reviews}
        if len(by_bug) != len(reviews) or set(ready) != set(by_bug):
            raise ValueError('Review each registered, non-demo Bug exactly once')
        case_data = cases.read()
        available = {row.id: row for row in case_data.targets}
        for review in reviews:
            existing = set(ready[review.bug_id].case_ids)
            if len(set(review.case_ids)) != len(review.case_ids):
                raise ValueError('Use unique case IDs: ' + review.bug_id)
            for case_id in review.case_ids:
                case = available.get(case_id)
                if case is None or (case.lifecycle != 'ready' and case_id not in existing):
                    raise ValueError('Link only registered test cases: ' + case_id)
        reports = cases.status()['reports'] if changes else {}
        planned = []
        for change in changes:
            review = by_bug.get(change.bug_id)
            if review is None or ready[change.bug_id].status == 'unverified':
                raise ValueError('Only a reviewed Bug can justify an expectation change: ' + change.bug_id)
            if change.case_id not in review.case_ids:
                raise ValueError('The changed case must be linked to the reviewed Bug: ' + change.case_id)
            case = available[change.case_id]
            if case.lifecycle != 'ready' or reports[case.id]['status'] != 'bound':
                raise ValueError('Resolve the case operation or bindings first: ' + case.id)
            bindings = assertion_bindings(reports[case.id], change.file)
            if not bindings:
                raise ValueError('Use a file bound to the case implementation or check: ' + change.file)
            planned.append({**change.model_dump(), 'binding_fingerprints': bindings})
        return {
            'reviews': [
                {**review.model_dump(), 'context': bug_context(ready[review.bug_id]),
                 'previous_case_ids': ready[review.bug_id].case_ids}
                for review in reviews
            ],
            'expectations': planned,
            'case_revision': case_data.revision,
        }

    def verify(self, path, bugs, cases):
        plan = json.loads(path.read_text())
        bug_rows = {row.id: row for row in bugs.read().targets}
        case_rows = {row.id: row for row in cases.read().targets}
        added = removed = 0
        for review in plan['reviews']:
            bug = bug_rows.get(review['bug_id'])
            if bug is None or bug_context(bug) != review['context']:
                raise ValueError('Bug review or scenario changed; replan: ' + review['bug_id'])
            desired, previous = set(review['case_ids']), set(review['previous_case_ids'])
            if bug.lifecycle != 'ready' or set(bug.case_ids) != desired:
                raise ValueError('Finish the planned Bug associations: ' + bug.id)
            if not desired <= case_rows.keys():
                raise ValueError('Some associated test cases were deleted; replan: ' + bug.id)
            added += len(desired - previous)
            removed += len(previous - desired)
        reports = cases.status()['reports'] if plan['expectations'] else {}
        for change in plan['expectations']:
            case = case_rows.get(change['case_id'])
            if (case is None or case.lifecycle != 'ready' or reports[case.id]['status'] != 'bound'
                    or case.completed_revision <= plan['case_revision']):
                raise ValueError('Finish and verify the test case bindings: ' + change['case_id'])
            old = change['binding_fingerprints']
            current = assertion_bindings(reports[case.id], change['file'])
            binding_changed = any(current[identity] != old[identity] for identity in old.keys() & current.keys())
            if change['expected'] not in expectations(case.timeline) or not binding_changed:
                raise ValueError('Verify the updated assertion source and timeline expectation: ' + case.id)
        return {'reviewed_bugs': len(plan['reviews']), 'links_added': added,
                'links_removed': removed, 'expectations_checked': len(plan['expectations'])}
