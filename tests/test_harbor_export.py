import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


h = module('exporter', ROOT/'harbor_export.py')
capture = module('collector_fixture', ROOT/'task_trace.py')
grader = module('grader', ROOT/'harbor_support/grade.py')


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        work = self.root/'workspace'; work.mkdir()
        (work/'input.csv').write_text('value\n1\n')
        archive = capture.start(work, self.root/'archives', 'Synthetic fixture', 1)
        native = self.root/'native.jsonl'
        native.write_text('\n'.join(json.dumps({'type':'response_item','payload':{'type':'message','role':role,'content':[]}}) for role in ('user','assistant'))+'\n')
        self.first = archive.rows()[1]['seq']
        self.runs, self.references = [], []
        with archive.lock():
            for n in (1, 2):
                if n == 2:
                    attachment = archive.append('attachment', {'label':'later protocol', **archive.blob(b'new protocol')})
                    self.attachment = attachment['seq']
                run = archive.append('run_start', {'run_id':str(n), 'prompt':'Synthetic question '+str(n)})
                self.runs.append(run['seq'])
                (work/'result.json').write_text(json.dumps({'value':n}))
                (work/'report.md').write_text('Synthetic report version '+str(n))
                archive.append('exec_event', {'run_id':str(n),'event':{'type':'turn.completed'}})
                archive.append('run_end', {'run_id':str(n),'session_id':'synthetic','exit_code':0,'interrupted':False,'stdout':archive.blob(b''),'stderr':archive.blob(b'')})
                self.references.append(archive.snapshot('after_run')['seq'])
            archive.transcript(native,'synthetic')
        self.assertFalse(capture.finish(archive)['gaps'])
        self.archive = archive
        self.source = h.Source(archive.path)
        self.recipe = h.draft(self.source)
        self.recipe['review'] = 'Synthetic fixture explicitly reviewed for converter tests.'
        self.recipe['environment']['review'] = 'Local files only; no extra dependencies.'
        for i, stage in enumerate(self.recipe['stages']):
            stage['outputs'] = ['result.json','report.md']
            stage['reference_review'] = 'Synthetic test reference, not real expert data.'
            artifact_hash = archive.rows()[self.references[i]]['data']['files']['report.md']['sha256']
            stage['rubric'] = {'total_points':100, 'criteria':[
                {'id':'number','points':60,'description':'Numerical result','check':{'type':'json_number','file':'result.json','path':['value'],'expected':i+1,'abs_tol':1e-10}},
                {'id':'reason','points':40,'description':'Reviewed explanation','check':{'type':'manual','file':'report.md'}}],
                'assessments':[{'criterion_id':'reason','artifact_sha256':artifact_hash,'passed':True,'reviewer':'Synthetic fixture','reviewer_role':'curator','reason':'Synthetic known report'}]}
        self.recipe['stages'][0]['inputs'] = [{'destination':'input.csv','source':{'kind':'snapshot','seq':self.first,'path':'input.csv'}}]
        self.recipe['stages'][1]['inputs'] = [{'destination':'protocol.txt','source':{'kind':'attachment','seq':self.attachment}}]

    def tearDown(self):
        self.tmp.cleanup()

    def export(self, recipe=None, name='bundle', trace=False):
        path=self.root/name
        h.export(self.source, recipe or self.recipe, path, trace)
        return path

    def reference_dir(self, bundle, stage='turn-1'):
        return bundle/'task/steps'/stage/'solution/files'

    def test_multistage_separates_late_inputs_and_private_trace(self):
        bundle=self.export(trace=True)
        task=bundle/'task'
        self.assertFalse((task/'steps/turn-1/workdir/protocol.txt').exists())
        self.assertEqual((task/'steps/turn-2/workdir/protocol.txt').read_bytes(),b'new protocol')
        self.assertFalse((task/'evidence').exists())
        self.assertTrue((bundle/'evidence/events.jsonl').exists())
        self.assertEqual(h.Source(bundle/'evidence').head,self.source.head)
        self.assertNotIn('COPY',(task/'environment/Dockerfile').read_text())
        self.assertIn('environment_mode = "separate"',(task/'task.toml').read_text())
        self.assertIn('network_mode: none',(task/'environment/docker-compose.yaml').read_text())
        self.assertFalse((task/'instruction.md').exists())

    def test_reference_bytes_and_scores(self):
        bundle=self.export()
        for i,s in enumerate(self.recipe['stages']):
            refdir=self.reference_dir(bundle,s['id'])
            result=grader.grade(s['rubric'],refdir)
            self.assertEqual(result['score'],100)
            expected=self.archive.rows()[self.references[i]]['data']['files']['report.md']['sha256']
            self.assertEqual(h.sha((refdir/'report.md').read_bytes()),expected)

    def test_no_trace_copied_by_default_and_reproducible_manifest(self):
        a=self.export(name='a');b=self.export(name='b')
        self.assertFalse((a/'evidence').exists())
        self.assertEqual(h.read_json(a/'provenance.json'),h.read_json(b/'provenance.json'))

    def test_source_corruption_rejected(self):
        ref=self.archive.rows()[1]['data']['files']['input.csv']['sha256']
        (self.archive.path/'objects'/ref).write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError,'integrity'):h.Source(self.archive.path)

    def test_stale_report_and_head_rejected(self):
        r=copy.deepcopy(self.recipe);r['archive_head']='0'*64
        with self.assertRaisesRegex(ValueError,'mismatch'):self.export(r)
        report=h.read_json(self.archive.path/'report.json');report['verified_through_hash']='0'*64
        (self.archive.path/'report.json').write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError,'stale'):h.Source(self.archive.path)

    def test_future_input_rejected(self):
        r=copy.deepcopy(self.recipe);r['stages'][0]['inputs']=r['stages'][1]['inputs']
        with self.assertRaisesRegex(ValueError,'future source'):self.export(r)

    def test_future_reference_rejected(self):
        r=copy.deepcopy(self.recipe);r['stages'][0]['reference_snapshot']=self.references[1]
        with self.assertRaisesRegex(ValueError,'reference snapshot'):self.export(r)

    def test_unassessed_reference_rejected(self):
        r=copy.deepcopy(self.recipe);r['stages'][0]['rubric']['assessments']=[]
        with self.assertRaisesRegex(ValueError,'unassessed'):self.export(r)

    def test_new_manual_answer_is_pending_not_zero_or_old_score(self):
        bundle=self.export();work=self.reference_dir(bundle)
        (work/'report.md').write_text('An unfamiliar new answer')
        result=grader.grade(self.recipe['stages'][0]['rubric'],work)
        self.assertEqual(result['status'],'pending_review')
        self.assertIsNone(result['score']);self.assertIsNone(result['reward'])
        self.assertEqual(result['known_points'],60)

    def test_wrong_number_earns_only_other_items(self):
        work=self.reference_dir(self.export())
        (work/'result.json').write_text('{"value": 0}')
        result=grader.grade(self.recipe['stages'][0]['rubric'],work)
        self.assertEqual(result['score'],40)
        self.assertEqual([c['points'] for c in result['criteria']],[0,40])

    def test_missing_outputs_score_zero_and_nan_bool_do_not_pass(self):
        empty=self.root/'empty';empty.mkdir()
        self.assertEqual(grader.grade(self.recipe['stages'][0]['rubric'],empty)['score'],0)
        work=self.reference_dir(self.export())
        for value in ('NaN','true','Infinity','9'*1000):
            (work/'result.json').write_text('{"value":'+value+'}')
            self.assertEqual(grader.grade(self.recipe['stages'][0]['rubric'],work)['score'],40)

    def test_unsafe_paths_and_input_output_collision_rejected(self):
        for path in ('../outside','/absolute','nested/../../outside','result.json','RESULT.JSON'):
            r=copy.deepcopy(self.recipe);r['stages'][0]['inputs'][0]['destination']=path
            with self.assertRaises(ValueError):self.export(r)
        self.assertFalse((self.root/'outside').exists())

    def test_draft_environment_and_bad_totals_block_export(self):
        with self.assertRaisesRegex(ValueError,'draft'):self.export(h.draft(self.source))
        r=copy.deepcopy(self.recipe);r['environment']['profile']='external-mcp'
        with self.assertRaisesRegex(ValueError,'environment'):self.export(r)
        r=copy.deepcopy(self.recipe);r['stages'][0]['rubric']['total_points']=99
        with self.assertRaisesRegex(ValueError,'sum'):self.export(r)

    def test_export_refuses_overwrite(self):
        out=self.export()
        with self.assertRaisesRegex(ValueError,'already exists'):self.export()
        self.assertTrue((out/'task/task.toml').exists())

    def test_grade_does_not_execute_submission_and_rejects_symlink(self):
        work=self.reference_dir(self.export())
        marker=self.root/'executed'
        (work/'payload.py').write_text('from pathlib import Path\nPath('+repr(str(marker))+').touch()')
        grader.grade(self.recipe['stages'][0]['rubric'],work)
        self.assertFalse(marker.exists())
        (work/'result.json').unlink();(work/'result.json').symlink_to(work/'report.md')
        self.assertEqual(grader.grade(self.recipe['stages'][0]['rubric'],work)['score'],40)

    def test_score_summary_distinguishes_pending_from_empty_harbor_mean(self):
        trial=self.root/'trial'
        directory=trial/'steps/turn-1/verifier';directory.mkdir(parents=True)
        (trial/'result.json').write_text('{"metrics":[{"mean":0.0}]}')
        (directory/'scorecard.json').write_text(json.dumps({'status':'pending_review','score':None,'total_points':100,'known_points':60}))
        result=h.score_summary(self.recipe,trial)
        self.assertEqual(result['status'],'pending_review')
        self.assertIsNone(result['reward']);self.assertIsNone(result['score_out_of_100'])
        self.assertEqual(result['stages'][1]['status'],'not_scored')

    def test_score_summary_respects_aggregation_and_incomplete_stages(self):
        trial=self.root/'trial';trial.mkdir()
        self.assertEqual(h.score_summary(self.recipe,trial)['status'],'incomplete')
        for stage,score in [('turn-1',80),('turn-2',60)]:
            d=trial/'steps'/stage/'verifier';d.mkdir(parents=True)
            (d/'scorecard.json').write_text(json.dumps({'status':'scored','score':score,'total_points':100,'known_points':score}))
        self.assertEqual(h.score_summary(self.recipe,trial)['score_out_of_100'],70)
        self.recipe['reward_strategy']='final'
        self.assertEqual(h.score_summary(self.recipe,trial)['score_out_of_100'],60)


if __name__ == '__main__':
    unittest.main()
