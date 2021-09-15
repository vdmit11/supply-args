from contextvars import ContextVar

import pytest

from supply_args import supply_args


def test__supply_args_var_objects():
    timezone_var = ContextVar("my_project.namespace.timezone")
    locale_var = ContextVar("my_project.namespace.locale")

    @supply_args(timezone_var, locale_var)
    def _get_values(locale, timezone="UTC"):
        return (locale, timezone)

    # pylint: disable=no-value-for-parameter
    with pytest.raises(TypeError):
        _get_values()

    locale_var.set("en")

    assert _get_values() == ("en", "UTC")

    assert _get_values(locale="en_GB") == ("en_GB", "UTC")
    assert _get_values(timezone="GMT") == ("en", "GMT")
    assert _get_values("en_GB", "GMT") == ("en_GB", "GMT")


def test__args_from_arbitrary_object_attributes():
    class SomeObject:
        locale = "en"

    some_object = SomeObject()

    @supply_args(some_object)
    def _get_values(locale=None, timezone=None):
        return (locale, timezone)

    assert _get_values() == ("en", None)
    assert _get_values(timezone="UTC") == ("en", "UTC")

    # pylint: disable=attribute-defined-outside-init
    some_object.locale = "en_GB"
    assert _get_values() == ("en_GB", None)

    some_object.timezone = "GMT"
    assert _get_values() == ("en_GB", "GMT")


def test__args_from_getter_function():
    storage = {}

    def _get_current_locale(default):
        return storage.get("locale", default)

    def _get_current_timezone(default):
        return storage.get("timezone", default)

    @supply_args(locale=_get_current_locale, timezone=_get_current_timezone)
    def _get_values(locale=None, timezone=None):
        return locale, timezone

    assert _get_values() == (None, None)
    assert _get_values(timezone="UTC") == (None, "UTC")

    storage["timezone"] = "GMT"
    assert _get_values() == (None, "GMT")

    storage["locale"] = "en_GB"
    assert _get_values() == ("en_GB", "GMT")

    assert _get_values("en", "UTC") == ("en", "UTC")
    assert _get_values(locale="en", timezone="UTC") == ("en", "UTC")


def test__error_is_raised__for_non_existent_parameter():
    with pytest.raises(AssertionError):
        # check simple `name=source` form the decorator
        @supply_args(non_existent_parameter=lambda default: default)
        def _get_values(locale=None, timezone=None):
            return locale, timezone

    with pytest.raises(AssertionError):
        # check a more complex form, with multiple parameter names
        @supply_args(
            {
                "source": lambda default: default,
                "names": ["locale", "timezone", "non_existent_parameter"],
            }
        )
        def _get_values_2(locale=None, timezone=None):
            return locale, timezone


def test__only_keyword_parameters_are_allowed():
    class Storage:
        pass

    # pylint: disable=attribute-defined-outside-init
    storage = Storage()

    with pytest.raises(AssertionError):
        storage.args = [1, 2, 3]

        @supply_args(args=storage)
        def _args(*args):
            return args

    with pytest.raises(AssertionError):
        storage.kwargs = {"foo": 1, "bar": 2}

        @supply_args(kwargs=storage)
        def _kwargs(**kwargs):
            return kwargs

    # Unfortunately, Positional-Only parameters cannot be tested,
    # because the syntax was added in Python v3.8,
    # whereas tests are executed in Python v3.7
    #
    # with pytest.raises(AssertionError):
    #     storage.positional_only_arg = 'positional only arg'
    #
    #     @supply_args(positional_only_arg=storage)
    #     def _positional_only(positional_only_arg=None, /, keyword_or_positional_arg=None):
    #         return positional_only_arg

    # ...but, at least we can test that KEYWORD_ONLY parameters work
    # (keyword-only parameters they were added in Python v3.0)
    storage.keyword_only_arg = "keyword only arg"

    @supply_args(keyword_only_arg=storage)
    def _keyword_only(keyword_or_positional_arg=None, *, keyword_only_arg=None):
        return keyword_only_arg

    assert _keyword_only() == "keyword only arg"
