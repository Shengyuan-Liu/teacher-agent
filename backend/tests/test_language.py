from app.agents.language import answer_language, instruction


class TestScriptIsDefinitive:
    def test_kana_means_japanese_even_with_han_characters(self):
        # Han alone would read as Chinese; the kana settles it.
        assert answer_language("バックプロパゲーションの実装方法") == "Japanese"
        assert answer_language("勾配降下法とは何ですか") == "Japanese"

    def test_han_without_kana_is_chinese(self):
        assert answer_language("什么是反向传播") == "Chinese"

    def test_other_scripts(self):
        assert answer_language("역전파란 무엇인가") == "Korean"
        assert answer_language("что такое градиентный спуск") == "Russian"


class TestLatinScript:
    def test_a_long_english_question_is_named(self):
        assert answer_language("how to implement backward propagation via pytorch") == "English"

    def test_a_long_french_question_is_named(self):
        assert (
            answer_language("Comment implémenter la rétropropagation avec PyTorch en pratique")
            == "French"
        )

    def test_short_latin_text_is_not_guessed(self):
        # Too short to tell German from English; a wrong name is worse than none.
        assert answer_language("Was ist das?") is None
        assert answer_language("hi") is None


class TestInstruction:
    def test_names_the_language_when_known(self):
        assert "in English." in instruction("how to implement backward propagation via pytorch")

    def test_falls_back_without_naming_a_language(self):
        assert "same language as the question" in instruction("hi")

    def test_empty_input(self):
        assert answer_language("") is None
        assert "same language" in instruction("   ")
