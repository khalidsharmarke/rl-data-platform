from slug import slugify


def test_empty_string():
    assert slugify("") == ""
