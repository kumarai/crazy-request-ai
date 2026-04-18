from app.support.retrieval_query import build_support_retrieval_query


def test_slow_internet_query_is_compacted_and_expanded():
    rewritten = build_support_retrieval_query(
        "Hi, my internet is pretty slow. Why?"
    )
    assert "hi" not in rewritten.split()
    assert "pretty" not in rewritten.split()
    assert "internet" in rewritten
    assert "slow" in rewritten
    assert "speed" in rewritten
    assert "slow internet" in rewritten


def test_billing_query_gets_billing_terms():
    rewritten = build_support_retrieval_query(
        "Can you help me with my bill payment?"
    )
    assert "bill" in rewritten
    assert "payment" in rewritten
    assert "billing" in rewritten
    assert "invoice" in rewritten


def test_empty_query_is_left_alone():
    assert build_support_retrieval_query("") == ""
