from slug import slugify


def test_punctuation_is_dropped():
    assert slugify("Hello, World!") == "hello-world"


def test_apostrophes_join_the_word():
    assert slugify("It's done.") == "its-done"


def test_a_word_of_only_punctuation_disappears():
    assert slugify("this - that") == "this-that"


def test_plain_words_still_work():
    assert slugify("Plain Words") == "plain-words"
