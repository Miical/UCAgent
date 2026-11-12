"""Exercise panel-authoritative planning and real source-binding acceptance."""
from target_test_support import inspect_fixture, bind_target

import json
from pathlib import Path
import tempfile
import unittest

from flowcanvas.planning_targets import PlanningTargetsChecker
from flowcanvas.targets import Targets
from ucagent.util.functions import get_unity_chip_doc_marks


class PanelAcceptanceTests(unittest.TestCase):
    """Use the real target store, parser and binding transactions in temporary projects."""

    def setUp(self):
        """Create a complete panel tree independent of its generated compatibility index."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.work = self.root / "workspace"
        self.summary = self.work / "summary"
        self.summary.mkdir(parents=True)
        (self.root / "request.json").write_text(json.dumps({"project_root":str(self.project),"dut":"Probe"}))
        self.store = Targets(self.project)
        self.ids = []
        self.mapping = {}
        path = []
        for kind, tag, name in (("group","FG-API","Interface"),("point","FC-ADD","Addition"),
                                ("testpoint","CK-ZERO","Zero inputs")):
            row = self.store.create(kind,name,self.ids[-1] if self.ids else None,
                                    self.store.read().revision,description=name+" behavior")
            self.ids.append(row["id"])
            path.append(tag)
            self.mapping["/".join(path)] = row["id"]
        self.map_file = self.summary / "flowcanvas-target-map.json"
        self.map_file.write_text(json.dumps(self.mapping))
        self.doc = self.summary / "Probe_functions_and_checks.md"
        self.cov = self.summary / "tests/Probe_function_coverage_def.py"
        self.cov.parent.mkdir()
        self.cov.write_text("def create_group():\n    return []\n\ndef observe_point(dut):\n    return dut.a\n\ndef check_zero(dut):\n    return dut.a == 0\n")
        self.published = self.project / "verification/coverage/Probe" / self.cov.name
        self.published.parent.mkdir(parents=True)
        self.published.write_bytes(self.cov.read_bytes())

    def checker(self, **kwargs):
        """Attach a checker using the same relative paths as resolved workflow configuration."""
        checker = PlanningTargetsChecker("summary/Probe_functions_and_checks.md",**kwargs)
        checker.workspace = str(self.work)
        return checker

    def bind_all(self):
        """Bind three distinct parsed implementation nodes using public transactions."""
        for identity, symbol in zip(self.ids,("create_group","observe_point","check_zero")):
            file = str(self.published.relative_to(self.project))
            parsed = inspect_fixture(self.store,[file])
            node = next(n for n in parsed[0]["nodes"] if n["name"] == symbol)
            bind_target(self.store,identity,{"file":file,"start":node["start"],
                                     "fingerprint":node["fingerprint"],"role":"coverage"},
                            self.store.read().revision)

    def test_panel_is_authoritative_and_native_index_is_compatible(self):
        """Panel text replaces manually authored indexes and survives native label parsing."""
        self.doc.write_text("Invented document-only test point")
        ok,result = self.checker().do_check()
        self.assertTrue(ok,result)
        self.assertNotIn("Invented",self.doc.read_text())
        self.assertIn("Zero inputs behavior",self.doc.read_text())
        self.assertEqual(get_unity_chip_doc_marks(str(self.doc),"CK",1),["FG-API/FC-ADD/CK-ZERO"])
        target = self.store.read().targets[-1]
        self.store.update(target.id,{"name":"Updated scenario"},self.store.read().revision)
        self.assertTrue(self.checker().do_check()[0])
        self.assertIn("Updated scenario",self.doc.read_text())

    def test_document_alone_missing_map_or_wrong_parent_cannot_pass(self):
        """Document content cannot substitute for project records or correct identity mapping."""
        self.map_file.unlink()
        self.doc.write_text("<FG-API>\n<FC-ADD>\n<CK-ZERO>")
        self.assertFalse(self.checker().do_check()[0])
        bad=dict(self.mapping)
        value=bad.pop("FG-API/FC-ADD/CK-ZERO")
        bad["FG-API/FC-OTHER/CK-ZERO"]=value
        self.map_file.write_text(json.dumps(bad))
        self.assertFalse(self.checker().do_check()[0])
        self.map_file.write_text(json.dumps(self.mapping))
        self.store.path.unlink()
        self.assertFalse(self.checker().do_check()[0])

    def test_missing_children_unmapped_or_unfinished_cards_are_rejected(self):
        """The index cannot silently drop extra, unfinished or empty-parent panel cards."""
        data=self.store.read()
        original=data.model_dump_json()
        data.targets.pop()
        self.store.path.write_text(data.model_dump_json())
        self.assertFalse(self.checker().do_check()[0])
        self.store.path.write_text(original)
        for change in ("creating","deleting"):
            data=self.store.read()
            data.targets[-1].lifecycle=change
            self.store.path.write_text(data.model_dump_json())
            self.assertFalse(self.checker().do_check()[0])
        self.store.path.write_text(original)
        self.store.create("group","Extra group",None,self.store.read().revision,description="Extra")
        self.assertFalse(self.checker().do_check()[0])

    def test_real_bindings_and_published_source_required(self):
        """All three levels need distinct real bindings to the accepted published implementation."""
        checker=self.checker(bindings=True,cov_file="summary/tests/"+self.cov.name)
        self.assertEqual(checker.do_check()[1]["error_code"],"PANEL_BINDINGS_REQUIRED")
        self.bind_all()
        ok,result=checker.do_check()
        self.assertTrue(ok,result)
        self.cov.write_text(self.cov.read_text()+"\nextra = True\n")
        self.assertFalse(checker.do_check()[0])

    def test_changed_missing_conflicting_and_unparseable_source_are_rejected(self):
        """Every failure reported by live binding validation blocks acceptance."""
        self.bind_all()
        checker=self.checker(bindings=True,cov_file="summary/tests/"+self.cov.name)
        original=self.published.read_text()
        for content in (original.replace("return dut.a == 0","return dut.a == 1"),
                        original.replace("# @flowcanvas id=","# removed id="),
                        original+"\n"+next(line for line in original.splitlines()
                                                         if "@flowcanvas" in line)+"\nx = 1\n",
                        original+"\ninvalid syntax @!"):
            self.published.write_text(content)
            self.assertFalse(checker.do_check()[0])
        self.published.unlink()
        self.assertFalse(checker.do_check()[0])

    def test_rtl_role_and_unrelated_helpers_cannot_substitute_for_model_nodes(self):
        """A bound file must represent implementation in the published accepted model."""
        self.bind_all()
        checker=self.checker(bindings=True,cov_file="summary/tests/"+self.cov.name)
        data=self.store.read()
        data.targets[-1].bindings[0].role="rtl"
        self.store.path.write_text(data.model_dump_json())
        self.assertFalse(checker.do_check()[0])
        data.targets[-1].bindings[0].role="coverage"
        helper=self.project/"helper.py"
        helper.write_text("\n".join(
            line for line in self.published.read_text().splitlines()
            if "@flowcanvas" not in line or data.targets[-1].bindings[0].id in line
        )+"\n")
        data.targets[-1].bindings[0].file="helper.py"
        # Keep each marker unique across all bound files.
        content=self.published.read_text()
        marker=data.targets[-1].bindings[0].id
        self.published.write_text(content.replace("# @flowcanvas id="+marker,"# moved "+marker))
        self.store.path.write_text(data.model_dump_json())
        self.assertEqual(self.store.status()["reports"][self.ids[-1]]["status"],"bound")
        self.assertFalse(checker.do_check()[0])

    def test_batch_checks_can_progress_before_final_binding_acceptance(self):
        """Intermediate native batch checks proceed, but Complete requires every binding."""
        checker=self.checker(bindings=True,complete_only=True,cov_file="summary/tests/"+self.cov.name)
        self.assertTrue(checker.do_check(is_complete=False)[0])
        self.assertFalse(checker.do_check(is_complete=True)[0])
        self.bind_all()
        self.assertTrue(checker.do_check(is_complete=True)[0])
