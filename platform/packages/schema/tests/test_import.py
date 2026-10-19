import zm_schema


def test_package_imports() -> None:
    assert zm_schema.__version__ == "0.1.0"
    assert zm_schema.CURRENT_VERSION == 1
