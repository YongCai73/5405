import json
from meeting_copilot.evaluation import evaluate

def test_frozen_runner_controls_and_synthetic_ablations(tmp_path):
    path=tmp_path/'report.json';summary=evaluate(path,tmp_path/'work');r=json.loads(path.read_text())
    assert summary['skills_correct']==r['qa_questions']==22
    assert r['configurations']['skills']['future_leakage_cases']==0
    assert r['configurations']['no_snapshots']['future_leakage_cases']>0
    assert r['configurations']['no_folder']['correct']<22
    assert r['configurations']['direct']['same_retrieval_context_as_skills']
    assert r['configurations']['no_policy']['control_unauthorized_disclosures']==0
    assert r['configurations']['no_policy']['unauthorized_disclosures']==1
    assert r['security']['passed']==r['security']['total']==10
    assert r['native_meet2notes_baseline']['status']=='not measured'
