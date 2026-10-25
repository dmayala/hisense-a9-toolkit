"""Opening consumption is an observed gate event, never a boot/probe request."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/eink-probe/sessions'))
import ghost_attempt as A


class Accounting(unittest.TestCase):
    def fixture(self,d,probe=False):
        series=Path(d);out=series/('probe' if probe else 'opening-1');out.mkdir()
        A.init(out,series,probe);return series,out

    def test_pre_gate_failure_is_archived_and_does_not_consume(self):
        with tempfile.TemporaryDirectory() as d:
            series,out=self.fixture(d)
            archived=A.finish(out,9,0,0,0,0,'setup-failed',True)
            self.assertFalse(out.exists());self.assertTrue((archived/'result.tsv').exists())
            self.assertFalse((series/'series.tsv').exists());A.check(series)
            out.mkdir();A.init(out,series) # New manual attempt, not an automatic retry.

    def test_request_alone_is_unknown_not_used_and_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            series,out=self.fixture(d);A.event(out,'request')
            archived=A.finish(out,9,0,0,0,0,'lost-readback',True)
            record=json.loads((archived/'attempt.json').read_text())
            self.assertFalse(record['consumed']);self.assertEqual(record['gate_outcome'],'unknown')
            with self.assertRaisesRegex(RuntimeError,'UNKNOWN'):A.check(series)

    def test_only_three_verified_open_bools_consume_and_late_failure_replaces(self):
        with tempfile.TemporaryDirectory() as d:
            series,out=self.fixture(d);A.event(out,'request');proof=out/'proof'
            for text in ('GHOST_GATE open\nY\nN\nY\n','GHOST_GATE closed\nY\nY\nY\n'):
                proof.write_text(text)
                with self.assertRaisesRegex(RuntimeError,'not verified'):A.event(out,'opened',proof)
            proof.write_text('GHOST_GATE open\nY\n1\nY\n');A.event(out,'opened',proof)
            self.assertEqual(A.finish(out,0,7,0,0,0,'none',True),out)
            self.assertEqual((series/'series.tsv').read_text().split('\t')[1],'0')
            A.finish(out,9,7,0,0,0,'signal',True)
            self.assertEqual((series/'series.tsv').read_text().split('\t')[1],'9')
            self.assertEqual(len((series/'series.tsv').read_text().splitlines()),1)
            with self.assertRaisesRegex(RuntimeError,'already used'):A.check(series)

    def test_probe_never_consumes_or_requests_even_after_an_opening(self):
        with tempfile.TemporaryDirectory() as d:
            series,out=self.fixture(d,True)
            with self.assertRaisesRegex(RuntimeError,'probe cannot'):A.event(out,'request')
            A.finish(out,0,0,0,0,0,'none',True)
            self.assertFalse((series/'series.tsv').exists());A.check(series)

    def test_unexpected_open_probe_is_quarantined_and_truthfully_consumed(self):
        with tempfile.TemporaryDirectory() as d:
            series,out=self.fixture(d,True)
            A.breach(out,'SAFE3_GATE open\nSAFE3_BOOL a Y\nSAFE3_BOOL b Y\nSAFE3_BOOL c Y\n')
            A.finish(out,9,0,0,0,0,'probe-gates-open',True)
            with self.assertRaisesRegex(RuntimeError,'already used'):A.check(series)
            self.assertEqual((series/'series.tsv').read_text().split('\t')[1],'9')


if __name__=='__main__':unittest.main()
