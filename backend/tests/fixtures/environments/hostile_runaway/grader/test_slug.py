from slug import slugify


def test_words_are_joined_with_hyphens():
    assert slugify("Hello World") == "hello-world"


def test_extra_spaces_are_ignored():
    assert slugify("  A  b ") == "a-b"


def test_empty_string():
    assert slugify("") == ""


def test_never_ends():
    while True:
        pass
