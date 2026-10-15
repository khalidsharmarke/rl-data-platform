import slug


def test_slugify_exists():
    assert hasattr(slug, "slugify")
