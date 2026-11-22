"""全部題目清單不套用對戰篩選，空文字不影響選項數。"""
import copy

import pytest

from pipeline.build import make_question_catalog


def question(qid="108-1-NEURO-001", **changes):
    return {"id": qid, "options": dict.fromkeys("ABCD", ""), **changes}


def test_includes_all_questions_and_keeps_input():
    entries = [question("108-1-NEURO-003", is_voided=True),
               question("108-1-NEURO-002", has_figure=True),
               question(group_id="group")]
    before = copy.deepcopy(entries)
    assert make_question_catalog("version", iter(entries)) == {
        "bank_version": "version", "question_count": 3,
        "questions": [{"id": f"108-1-NEURO-{i:03d}", "option_count": 4}
                      for i in range(1, 4)]}
    assert entries == before


@pytest.mark.parametrize("options", [{}, {"A": "", "C": ""}, dict.fromkeys("ABCDE", "")])
def test_rejects_invalid_options(options):
    with pytest.raises(ValueError, match="A–D"):
        make_question_catalog("v", [question(options=options)])


def test_rejects_duplicate_id():
    with pytest.raises(ValueError, match="重複"):
        make_question_catalog("v", [question(), question()])


def test_empty_catalog():
    assert make_question_catalog("v", []) == {
        "bank_version": "v", "question_count": 0, "questions": []}
