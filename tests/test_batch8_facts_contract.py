from services.facts_conflict import FactConflictClassifier


def test_fact_classifier_distinguishes_compatible_scoped_conflicts_and_supersedes():
    classifier = FactConflictClassifier()
    base = {"id": 7, "subject": "用户", "predicate": "喜欢", "object": "咖啡", "valid_from": 10.0, "valid_until": 20.0}

    assert classifier.classify({**base}, [base]).relation == "compatible"
    assert classifier.classify({**base, "object": "茶", "valid_from": 30.0}, [base]).relation == "scoped"
    assert classifier.classify({**base, "object": "茶"}, [base]).relation == "conflicts"
    assert classifier.classify(
        {**base, "object": "茶", "provenance": {"supersedes": True, "supersedes_fact_id": 7}},
        [base],
    ).relation == "supersedes"


def test_fact_classifier_does_not_conflict_across_predicates():
    classifier = FactConflictClassifier()
    existing = {"id": 1, "subject": "用户", "predicate": "喜欢", "object": "咖啡"}
    candidate = {"subject": "用户", "predicate": "讨厌", "object": "咖啡"}
    assert classifier.classify(candidate, [existing]).relation == "compatible"
