from app.search import filter_and_rank, matches

PRODUCT = ("AFF-A17", "Afficheur", "Samsung Galaxy", "A17", "OLED")


def test_words_in_any_order_and_partial():
    for query in ("samsung a17", "a17 samsung", "sam a17", "aff a17", "AFF-A17", "galaxy oled"):
        assert matches(query, *PRODUCT), query


def test_all_words_required_and_no_typo_tolerance():
    assert not matches("samsung a18", *PRODUCT)
    assert not matches("samsng a17", *PRODUCT)


def test_accents_and_case_ignored():
    assert matches("ecran", "Écran iPhone")
    assert matches("ÉCRAN", "ecran iphone")


def test_empty_query_matches_everything():
    assert matches("   ", *PRODUCT)


def test_whole_and_prefix_matches_first():
    items = ["Samsung TA17", "Samsung A170", "Samsung Galaxy A17"]
    assert filter_and_rank("a17", items, lambda item: (item,)) == [
        "Samsung Galaxy A17", "Samsung A170", "Samsung TA17",
    ]
