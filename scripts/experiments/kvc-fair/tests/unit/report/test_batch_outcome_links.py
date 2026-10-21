"""Small synthetic evidence/DOM fragments; no benchmark or full report rendering."""
from copy import deepcopy
import csv
import hashlib
from html.parser import HTMLParser
import io
import json
from pathlib import Path
from tests.paths import TOOL_ROOT
import subprocess
import tempfile
import unittest

from kvcbench.collect.analysis import batch as batch_analyze
from kvcbench.compat import reports as batch_report
import tests.unit.collect.test_batch_failures as failure_fixtures


HERE = TOOL_ROOT
IDENTITY = "r0-datasystem/b16-exist_hit"
OUTCOME = "results/r0-datasystem/case-outcomes/b16-exist_hit.json"


def zero_success(suite):
    path = suite / (IDENTITY + ".json")
    raw = json.loads(path.read_text())
    for client in raw["clients"]:
        client.update(success=0, histogram={}, latency_sum_ns=0, latency_max_ns=0,
                      per_second={}, validated_keys=0)
    raw.update(mean_us=None, max_us=0.0)
    failure_fixtures.sync_failure(raw)
    path.write_text(json.dumps(raw))
    outcome = suite / Path(*Path(OUTCOME).parts[1:])
    record = json.loads(outcome.read_text())
    record["result_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    outcome.write_text(json.dumps(record))
    events = [json.loads(line) for line in (suite / "events.jsonl").read_text().splitlines()]
    for event in events:
        if event.get("event") == "measure_failed" and event.get("identity") == IDENTITY:
            event["success"] = 0
    (suite / "events.jsonl").write_text("\n".join(map(json.dumps, events)) + "\n")


class Rows(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.rows, self.current, self.cell, self.pre = [], None, None, False
        self.feed(source)

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "tr":
            self.current = {"links": [], "cells": [], "text": "", "errors": "", "status": None}
        elif self.current is not None:
            if tag == "a":
                self.current["links"].append(attributes.get("href"))
            if tag == "td":
                self.cell = []
                if attributes.get("class", "").startswith("status-"):
                    self.current["status"] = attributes["class"]
            self.pre = self.pre or tag == "pre"

    def handle_data(self, text):
        if self.current is not None:
            self.current["text"] += text
            if self.cell is not None:
                self.cell.append(text)
            if self.pre:
                self.current["errors"] += text

    def handle_endtag(self, tag):
        if tag == "pre":
            self.pre = False
        elif tag == "td" and self.cell is not None:
            value = "".join(self.cell)
            self.current["cells"].append(value)
            if self.current["status"] and "status_text" not in self.current:
                self.current["status_text"] = value
            self.cell = None
        elif tag == "tr" and self.current is not None:
            self.rows.append(self.current)
            self.current = None


def node(code, data):
    result = subprocess.run(["node", "-e", code], input=json.dumps(data), text=True,
                            capture_output=True, check=True, timeout=10)
    return json.loads(result.stdout)


RENDER = r"""
const fs=require('node:fs'),vm=require('node:vm');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const lines=fs.readFileSync(input.source,'utf8').split('\n');
const prefixes=['const D=','const names=','const $=','const number=','const actual=',
                'function renderTable()','function renderFailures()'];
const fragments=prefixes.map(prefix=>{
 const own=lines.filter(line=>line.startsWith(prefix));
 if(own.length!==1)throw Error('Exact production fragment missing: '+prefix);
 return own[0];
});
const nodes={operation:{value:'get'},results:{innerHTML:''},
 'native-failures':{hidden:true},'failure-results':{innerHTML:''}};
const context={window:{KVC_BATCH_DATA:input.data},document:{getElementById:id=>nodes[id]}};
vm.runInNewContext(fragments.join('\n')+'\nrenderFailures();',context);
const tables={};for(const op of ['put','upsert','get','exist_hit','exist_miss']){
 nodes.operation.value=op;vm.runInNewContext('renderTable();',context);tables[op]=nodes.results.innerHTML;
}
process.stdout.write(JSON.stringify({tables,failureHTML:nodes['failure-results'].innerHTML,
 hidden:nodes['native-failures'].hidden}));
"""


QA_PREDICATES = r"""
const fs=require('node:fs'),vm=require('node:vm');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const lines=fs.readFileSync(input.source,'utf8').split('\n');
const select=label=>{const own=lines.filter(line=>line.includes('check(')&&line.includes(label));
 if(own.length!==1)throw Error('Exact production QA predicate missing: '+label);return own[0];};
const checks=[],scope={...input.context,check:(name,pass)=>checks.push({name,pass})};
try{vm.runInNewContext(select('failure table retains exact errors and receipts')+'\n'+
 select('table distinguishes every original status'),scope);
 process.stdout.write(JSON.stringify({checks}));
}catch(error){process.stdout.write(JSON.stringify({checks,error:String(error)}));}
"""


class OutcomeEvidenceTests(unittest.TestCase):
    def fixture(self, root):
        return failure_fixtures.CompleteFailureEvidenceTests().fixture(root)[0]

    def test_verified_relative_receipt_link_is_exported_only_for_failed_row(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); suite = self.fixture(root)
            analysis = batch_analyze.analyze(suite)
            failed = next(row for row in analysis["rows"] if row["identity"] == IDENTITY)
            self.assertEqual(failed["outcome_evidence"], OUTCOME)
            self.assertTrue((suite / Path(*Path(OUTCOME).parts[1:])).is_file())
            self.assertTrue(all("outcome_evidence" not in row for row in analysis["rows"] if row["identity"] != IDENTITY))
            out = root / "small-export"; out.mkdir()
            batch_analyze.export(analysis, out)
            rows = list(csv.DictReader(io.StringIO((out / "summary.csv").read_text())))
            self.assertEqual(next(row for row in rows if row["identity"] == IDENTITY)["outcome_evidence"], OUTCOME)
            self.assertTrue(all(not row["outcome_evidence"] for row in rows if row["identity"] != IDENTITY))

    def test_missing_or_tampered_receipt_never_gets_a_verified_link(self):
        for mode in ["missing", "hash", "exit"]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                suite = self.fixture(Path(directory))
                path = suite / Path(*Path(OUTCOME).parts[1:])
                if mode == "missing":
                    path.unlink()
                else:
                    raw = json.loads(path.read_text())
                    raw["result_sha256" if mode == "hash" else "returncode"] = "0" * 64 if mode == "hash" else 0
                    path.write_text(json.dumps(raw))
                row = next(row for row in batch_analyze.analyze(suite)["rows"] if row["identity"] == IDENTITY)
                self.assertEqual(row["status"], "invalid")
                self.assertFalse(row["evidence_verified"])
                self.assertNotIn("outcome_evidence", row)

    def test_zero_success_complete_matrix_keeps_receipt_and_null_performance(self):
        with tempfile.TemporaryDirectory() as directory:
            suite = self.fixture(Path(directory)); zero_success(suite)
            analysis = batch_analyze.analyze(suite)
            self.assertEqual(analysis["issues"], [])
            self.assertEqual(analysis["counts"], {"valid": 149, "failed": 1, "not_supported": 30})
            self.assertTrue(analysis["evidence_verified"])
            self.assertFalse(analysis["passed"])
            row = next(row for row in analysis["rows"] if row["identity"] == IDENTITY)
            self.assertEqual((row["status"], row["success"], row["failures"]), ("failed", 0, 8))
            self.assertEqual(row["outcome_evidence"], OUTCOME)
            self.assertEqual([row[key] for key in ["p50_us", "p95_us", "p99_us"]], [None] * 3)
            aggregate = next(row for row in analysis["aggregates"] if row["product"] == "datasystem"
                             and row["operation"] == "exist_hit" and row["batch"] == 16)
            self.assertEqual(aggregate["status"], "failed")
            self.assertTrue(all(item[key] is None for item in aggregate["metrics"].values()
                                for key in ["median", "min", "max", "cv_percent"]))
            self.assertTrue(batch_report.report_completion(analysis)["complete"])

    def test_actual_js_fragments_and_qa_predicates_preserve_zero_failure_links_and_status(self):
        with tempfile.TemporaryDirectory() as directory:
            suite = self.fixture(Path(directory)); zero_success(suite)
            analysis = batch_analyze.analyze(suite)
            failed = next(row for row in analysis["rows"] if row["identity"] == IDENTITY)
            failed["native_errors"][0]["errors"][0] += '</pre><script>untrusted_fixture()</script>&"'
            rendered = node(RENDER, {"source": str(HERE / "kvcbench/compat/assets/batch-report.js"), "data": analysis})
            failures = Rows(rendered["failureHTML"]).rows
            self.assertFalse(rendered["hidden"])
            self.assertEqual(len(failures), 1)
            self.assertEqual(set(failures[0]["links"]), {failed["evidence"], OUTCOME, failed["epoch_evidence"]})
            self.assertEqual(json.loads(failures[0]["errors"]), failed["native_errors"])
            self.assertNotIn("<script>", rendered["failureHTML"])
            self.assertEqual(failures[0]["cells"][2], "0 / 0")
            self.assertIn("0 / 0", failures[0]["cells"][3])
            for operation, html in rendered["tables"].items():
                rows = Rows(html).rows
                expected = [row for row in analysis["rows"] if row["operation"] == operation]
                self.assertEqual(len(rows), len(expected))
                for actual, original in zip(rows, expected):
                    self.assertIn(original["evidence"], actual["links"])
                    self.assertEqual(actual["status"], "status-" + original["status"])
                    if original["status"] == "failed":
                        self.assertTrue(all(value.startswith("不计性能") for value in actual["cells"][3:6]))
                table = [dict(status=row["status_text"], links=row["links"], text=row["text"]) for row in rows]
                context = {"width": 1440, "operation": operation, "operationRows": expected, "table": table,
                           "failedRows": [failed], "failureTable": {"hidden": rendered["hidden"], "rows": failures}}
                result = node(QA_PREDICATES, {"source": str(HERE / "tests/acceptance/legacy/qa_batch_report.mjs"), "context": context})
                self.assertNotIn("error", result)
                self.assertTrue(all(item["pass"] for item in result["checks"]), result)
                if operation == "exist_hit":
                    for mode in ["receipt_link", "errors", "status"]:
                        broken = deepcopy(context)
                        if mode == "receipt_link":
                            broken["failureTable"]["rows"][0]["links"].remove(OUTCOME)
                        elif mode == "errors":
                            broken["failureTable"]["rows"][0]["errors"] = "[]"
                        else:
                            next(row for row in broken["table"] if row["status"] == "原生失败")["status"] = "有效"
                        result = node(QA_PREDICATES, {"source": str(HERE / "tests/acceptance/legacy/qa_batch_report.mjs"), "context": broken})
                        self.assertTrue(any(not item["pass"] for item in result["checks"]), (mode, result))


if __name__ == "__main__":
    unittest.main()
