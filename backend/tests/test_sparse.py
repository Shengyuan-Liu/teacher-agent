from app.rag.sparse import tokenize


def test_keeps_identifiers_intact():
    assert "flm-419" in tokenize("The broker replies FLM-419 on a stale token")
    assert "cauchy-schwarz" in tokenize("the Cauchy-Schwarz inequality")


def test_lowercases_and_drops_punctuation():
    assert tokenize("Gradient, Descent!") == ["gradient", "descent"]


def test_splits_on_symbols_that_are_not_word_joiners():
    assert tokenize("f(x) = 2x") == ["f", "x", "2x"]


def test_empty_and_symbol_only_input():
    assert tokenize("") == []
    assert tokenize("$$ \\nabla ") == ["nabla"]
