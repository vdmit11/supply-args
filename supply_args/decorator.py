import contextvars
import dataclasses
import functools
import inspect
import re
from collections import abc
from typing import (
    Any,
    Callable,
    Collection,
    Iterable,
    NewType,
    Optional,
    Sequence,
    Tuple,
    TypeVar,
    Union,
)


# Some type definitions to make @supply_args decorator friednly towards static typing.
# It basically means that the decorator preserves the ReturnedValue inact.
# So when you call a decorated function, your IDE knows type of the returned value.
ReturnedValue = TypeVar("ReturnedValue")
WrappedFn = Callable[..., ReturnedValue]
Decorator = Callable[[WrappedFn], WrappedFn]


def supply_args(*sources, **per_arg_sources) -> Decorator:
    """Take arguments from context variables.

    Example usage::

        >>> from contextvars import ContextVar
        >>> timezone_var = ContextVar('my_project.timezone', default='UTC')
        >>> locale_var = ContextVar('my_project.locale', default='en')

        >>> @supply_args(locale=locale_var, timezone=timezone_var)
        ... def print_vars(*, locale, timezone):
        ...     print(f"locale: {locale}")
        ...     print(f"timezone: {timezone}")

        >>> print_vars()
        locale: en
        timezone: UTC
    """

    def _decorator__supply_args(wrapped_fn: WrappedFn) -> WrappedFn:
        rules = _generate_supply_rules(wrapped_fn, sources, per_arg_sources)
        rules_list = list(rules)

        @functools.wraps(wrapped_fn)
        def _wrapper__supply_args(*args, **kwargs) -> ReturnedValue:
            _execute_supply_rules(rules_list, args, kwargs)
            return wrapped_fn(*args, **kwargs)

        return _wrapper__supply_args

    return _decorator__supply_args


@dataclasses.dataclass(frozen=True)
class SupplySpec:
    """Structure for arguments of the ``@supply_args`` decorator.

    Arguments to the :func:`supply_args` decorator can come in several different forms.

    It is a bit of hassle to take into accoutn all these different forms of arguments everywhere,
    so they're all normalized, and converted to ``SupplySpec`` objects.

    So that, for example, this::

        @supply_args(registry)

    internally is converted to::

        [
            SupplySpec(source=registry)
        ]


    Keyword arguments are also converted to ``SupplySpec`` objects::

        @supply_args(
            locale=registry,
            timezone=registry,
            user_id=user_id_context_var,
        )
        # internally converted to:
        [
            SupplySpec(names=['locale'], source=registry),
            SupplySpec(names=['timezone'], source=registry),
            SupplySpec(names=['user_id'], source=user_id_context_var),
        ]


    ...and dictionaries are also converted to ``SupplySpec`` objects::

         @supply_args(
             {
                 'names': ['locale', 'timezone'],
                 'source': registry,
             },
             {
                 'names': ['user_id']
                 'source': user_id_context_var,
             }
         )
         # internally converted to:
         [
             SupplySpec(names=['locale', 'timezone'], source=registry),
             SupplySpec(names=['user_id'], source=user_id_context_var),
         ]

    Each argument to ``@supply_args()`` becomes a ``SupplySpec`` instance.
    This process is called here "normalization".

    So after the normalization procedure, all different forms of arguments become just a stream
    of ``SupplySpec`` objects, with well-known structure, which is easy to deal with
    (much easier than coding if/else branches to support several forms of arguments everywhere).

    .. NOTE::

      This class is for internal use only.
      It shouldn't be used outside of this module.

      However, you can still use it as documentation to get an idea of
      which keys you can use when you pass dictionary sources to the decorator.
    """

    source: Any
    """Source of vlaues that are supplied as arguments to functions.

    It can be:

      - a ``contextvars.ContextVar`` object
        (then its ``.get()`` method is called to obtain the value)

      - arbitrary object, e.g.: ``@supply_args(flask.g)``
        (then object attributes are supplied as arguments to the called functions)

      - arbitrary function, e.g.: ``@supply_args(locale=get_current_locale)``
        (then the function is just called to obtain the value)

    This list of behaviors can be extended via the :func:`choose_arg_getter_fn` function.
    """

    names: Optional[Collection[str]] = None
    """Names of function arguments that affected..

    A source may match to many arguments simultaneously, for example::

        @supply_args({
            'source': registry,
            'names': ['locale', 'timezone']
        })
        def fn(user_id=None, timezone=None, locale=None):
            pass

    In that example, ``locale`` and ``timezone`` are taken from ``registry``
    (whereas ``user_id`` argument is ignored).

    This ``names`` member is optional.
    If not provided, the ``names`` are chosen automatically, depending on the ``source``:

      - for ``ContextVar`` objects, the parameter name is guessed from ``ContextVar.name`` attribute
      - for ``ContextVarRegistry``, and all other types of sources, the ``names`` list by
        default is filled with all function parameters (so ALL arguments become affected).

    This name guessing behavior can be extended via the :func:`choose_arg_names` function.
    """


def _normalize_spec(name, source) -> SupplySpec:
    # There are 4 different ways to specify arguments for the @supply_args() decorator.
    # Here we recognize all 4 different cases, and convert them to SupplySpec object.
    #
    # So, after the normalization procedure, the messy arguments to @supply_args decorator
    # become just a sequence of SupplySpec objects, which is much easier to reason about,
    # since SupplySpec has a well-defined structure.
    if isinstance(source, dict):
        if name:
            # @supply_args(timezone={'source': registry})
            spec = SupplySpec(**source, names=[name])
        else:
            # @supply_args({'source': registry, 'names': ['locale', 'timezone']})
            spec = SupplySpec(**source)
    else:
        if name:
            # @supply_args(locale=registry, timezone=registry)
            spec = SupplySpec(source=source, names=[name])
        else:
            # @supply_args(registry)
            spec = SupplySpec(source=source)

    return spec


# We call "argument getter" a function of 1 parameter, that (roughly) looks like this:
#
#     def arg_getter(default):
#         return some_value or default
#
# Such getter function should somehow get a value,
# or return the ``default`` argument in case the value is not available.
Default = TypeVar("Default")
SupplyArgGetterFn = Callable[[Default], Union[Any, Default]]


# SupplyRuleTuple - a prepared "instruction" for the ``@supply_args`` decorator.
#
# Problem: there is some magic in how arguments of the :func:`inject_context_args` decorator
# are processed, and this magic is a bit slow.
#
# Well, maybe not really slow, but there is some overhead, which is summed up and becomes
# noticeable when you decorate a lot of functions.
#
# As a solution, we have a little premature optimization: the :func:`supply_args`
# decorator pre-processes its arguments, and sort of compiles them into rules.
#
# One such ``SupplyRuleTuple`` is a primitive instruction that (roughly) says:
#   - "call this getter function, and put the returned value to function arguments"
#
# And later on, when the decorated function is actually called, the prepared rules are executed.
#
# So the overhead of the ``@supply_args`` decorator is reduced down to just executing
# primitive rules (basically calling a bunch of prepared getter functions in sequence).
SupplyRuleTuple = NewType(
    "SupplyRuleTuple",
    Tuple[
        # parameter name
        str,
        # parameter position (None for KEYWORD_ONLY parameters)
        Optional[int],
        # parameter default value
        Any,
        # getter function that knows how to fetch the value for the parameter
        SupplyArgGetterFn,
    ],
)


# shortcuts, needed just to make code slightly more readable
_EMPTY = inspect.Parameter.empty
_KEYWORD_ONLY = inspect.Parameter.KEYWORD_ONLY
_POSITIONAL_OR_KEYWORD = inspect.Parameter.POSITIONAL_OR_KEYWORD


def _execute_supply_rules(rules: Sequence[SupplyRuleTuple], args: tuple, kwargs: dict):
    args_count = len(args)

    for (name, position, default, arg_getter_fn) in rules:
        # Argument is already passed? Nothing to do then.
        if name in kwargs:
            continue

        if (position is not None) and (position < args_count):
            continue

        # call the getter function that somehow fetches the value from the global variable
        value = arg_getter_fn(default)
        if value is _EMPTY:
            continue

        # inject the value into kwargs
        kwargs[name] = value


def _generate_supply_rules(
    wrapped_fn: Callable,
    sources: tuple,
    per_arg_sources: dict,
) -> Iterable[SupplyRuleTuple]:
    wrapped_fn_sig = inspect.signature(wrapped_fn)

    for name, source in per_arg_sources.items():
        spec = _normalize_spec(name, source)
        yield from _generate_supply_rules_for_single_source(spec, wrapped_fn, wrapped_fn_sig)

    for source in sources:
        spec = _normalize_spec(None, source)
        yield from _generate_supply_rules_for_single_source(spec, wrapped_fn, wrapped_fn_sig)


def _generate_supply_rules_for_single_source(
    spec: SupplySpec,
    wrapped_fn: Callable,
    wrapped_fn_sig: inspect.Signature,
) -> Iterable[SupplyRuleTuple]:
    # Ensure we get only valid parameter names in SupplySpec.names list.
    # Otherwise the code below will not work correctly.
    _check_param_names(spec, wrapped_fn_sig)

    # The expression below means that if parameter name is omitted, then match all parameters.
    #
    # That is, this example:
    #    @supply_args(timezone=registry)
    #    def get_values(locale, timezone, user_id): ...
    # will result in:
    #    names = ["timezone"]
    #
    # and this example:
    #    @supply_args(registry)
    #    def get_values(locale, timezone, user_id): ...
    # will result in:
    #    names = ["locale", "timezone", "user_id"]
    names = spec.names or _get_params_available_for_supply(wrapped_fn_sig)

    for position, param in enumerate(wrapped_fn_sig.parameters.values()):
        if param.name not in names:
            continue

        arg_getter_fn = make_supply_arg_getter(spec.source, param.name)
        if arg_getter_fn is SkipSupplyArgGetter:
            continue

        maybe_position = position if (param.kind is _POSITIONAL_OR_KEYWORD) else None
        default = param.default
        rule: SupplyRuleTuple = SupplyRuleTuple(
            (param.name, maybe_position, default, arg_getter_fn)
        )
        yield rule


_PARAM_KINDS_AVAILABLE_FOR_SUPPLY = (_KEYWORD_ONLY, _POSITIONAL_OR_KEYWORD)


def _check_param_names(spec: SupplySpec, wrapped_fn_sig: inspect.Signature):
    if not spec.names:
        return

    for name in spec.names:
        param = wrapped_fn_sig.parameters.get(name)
        if not param:
            raise AssertionError(f"no such parameter: {name}")

        # Why do we allow only keyword parameters?
        # Beacuse the _execute_supply_rules() function can only pass arguments by keyword.
        #
        # ...but why such limitation exists?
        # Well, it was added for simplicity and performance, because positional/variadic
        # arguments are tricky, there is a lot of corner cases.
        # So I decided to not support them to keep the code fast and readable.
        #
        # Besides, you rarely want to use @supply_args with positional arguments,
        # and you never want to use it with variable (e.g, *args/*kwargs) arguments.
        if param.kind not in _PARAM_KINDS_AVAILABLE_FOR_SUPPLY:
            kind = str(param.kind)
            allowed_kinds = [str(kind) for kind in _PARAM_KINDS_AVAILABLE_FOR_SUPPLY]
            raise AssertionError(
                f"Parameter '{name}' ({kind}) cannot be used with @supply_args "
                f"(only these kinds of parameters are allowed: {allowed_kinds})"
            )


def _get_params_available_for_supply(wrapped_fn_sig: inspect.Signature) -> Collection[str]:
    return {
        param.name
        for param in wrapped_fn_sig.parameters.values()
        if param.kind in _PARAM_KINDS_AVAILABLE_FOR_SUPPLY
    }


@functools.singledispatch
def make_supply_arg_getter(source: object, name: str) -> SupplyArgGetterFn:
    """Produce argument getter function.

    This is a helper function for the :func:`supply_args` decorator.

    The :func:`@supply_args` decorator analyzes function signature,
    and for each parameter, it calls this :func:`make_supply_arg_getter`.
    The resulting getter knows how to get value from a context variable or some other source.

    Take an example::

        >>> class Config:
        ...     locale: str = 'en'
        ...     timezone: str = 'UTC'

        >>> config = Config()

        >>> @supply_args(config)
        ... def print_values(user_id, locale, timezone, *args, **kwargs):
        ...     print(user_id, locale, timezone, args, kwargs)

    In this example above, the function will be triggered 3 times::

        make_supply_arg_getter(config, 'user_id')
        make_supply_arg_getter(config, 'locale')
        make_supply_arg_getter(config, 'timezone')

    That is, it is triggered for "normal" parameters, and NOT triggered for:

      - variable parameters (e.g., `*args, **kwargs`)
      - positional-only parameters (PEP 570, implemented in Python v3.8)

    Also note that this is a generic function, and its behavior depends on type of the 1st argument
    (see :func:`functools.singledispatch`). That is:

     - ``source=ContextVar()`` will result in ``ContextVar.get()`` call
     - ``source=lambda: ...`` will just call the source lambda
     - ``source=object()`` will result in ``getattr(object, name)``

    The list of implementations can be extended.
    Imagine that you want to get arguments from OS environment variables.
    Then you could do something like this:

        >>> from functools import partial
        >>> from supply_args import make_supply_arg_getter, SkipSupplyArgGetter

        >>> class EnvVarsStorage:
        ...    def __init__(self, environ: dict):
        ...        self.environ = environ
        ...
        ...    def is_set(self, name):
        ...        return name in self.environ
        ...
        ...    def get(self, name, default):
        ...        return self.environ.get(name, default)

        >>> @make_supply_arg_getter.register
        ... def make_supply_arg_getter_for_env_var(env_vars: EnvVarsStorage, name):
        ...     env_var_name = name.upper()
        ...
        ...     # Here we assume that `os.environ` is immutable.
        ...     # So, if the variable is not set here, then it will never be set, so we can skip it.
        ...     if not env_vars.is_set(env_var_name):
        ...         return SkipSupplyArgGetter
        ...
        ...     return partial(env_vars.get, env_var_name)

    That could allow to access OS environment variables as arguments using ``@supply_args``::

        >>> import os
        >>> import os.path

        # Let's assume that TMPDIR=/tmp.
        # I need it to be constant, because the example is executed (by doctest) on different hosts.
        >>> environ = os.environ.copy()
        >>> environ['TMPDIR'] = '/tmp'

        >>> env_vars_storage = EnvVarsStorage(environ)

        >>> @supply_args(env_vars_storage)
        ... def get_tmp_path(file_name, tmpdir):
        ...    return os.path.join(tmpdir, file_name)

        >>> get_tmp_path('foo')
        '/tmp/foo'

        >>> get_tmp_path('foo', tmpdir='/var/tmp')
        '/var/tmp/foo'
    """
    # A default implementation of make_supply_arg_getter(), that is triggered for just objects,
    # that don't have any special implementation.
    #
    # Here we just trigger getattr()
    #
    # That is, for example, if you do this:
    #
    #    @supply_args(some_object)
    #    def do_something_useful(foo, bar, baz):
    #        pass
    #
    # then, the effect will be (roughly) this:
    #
    #    do_something_useful(
    #        foo=getattr(some_object, 'foo')
    #        bar=getattr(some_object, 'bar')
    #        baz=getattr(some_object, 'baz')
    #    )
    return functools.partial(getattr, source, name)


@make_supply_arg_getter.register
def make_supply_arg_getter_for_callable(source: abc.Callable, name: str) -> SupplyArgGetterFn:
    # make_supply_arg_getter() implementation for lambdas and other callables
    #
    # That is, for example, if you do this:
    #
    #    @supply_args(
    #        locale=get_current_locale,
    #        timezone=get_current_timezone,
    #    )
    #    def do_something-_useful(locale='en', timezone='UTC'):
    #        ...
    #
    # Then, on call, the effect will be (roughly) this:
    #
    #     do_something_useful(
    #         locale=get_current_locale('en'),
    #         timezone=get_current_timezone('UTC'),
    #     )

    # So, here we just use the ``source`` callable as a getter function, as-is.
    # The value of the argument will be just the value returned by the getter function.
    return source


@make_supply_arg_getter.register
def make_supply_arg_getter_for_context_var(
    ctx_var: contextvars.ContextVar, name: str
) -> SupplyArgGetterFn:
    # Skip parameters, that don't match to context variable by name.
    #
    # This is needed for cases like this:
    #
    #    timezone_var = ContextVar('timezone', default='UTC')
    #
    #    @supply_args(locale_var)
    #    def do_something_useful(user_id, locale, timezone):
    #        pass
    #
    # Since parameter name is not specified, make_supply_arg_getter() is called 3 times:
    #    make_supply_arg_getter(timezone_var, 'user_id')
    #    make_supply_arg_getter(timezone_var, 'locale')
    #    make_supply_arg_getter(timezone_var, 'timezone')
    #
    # So the line below is needed to leave only the last getter
    if not _is_context_var_matching_to_param(ctx_var, name):
        return SkipSupplyArgGetter

    def _get_ctxvar_value_or_default(default):
        # Why try/except instead of just ctx_var.get(default)?
        #
        # Because there is also ContextVar's internal default value
        # (supplied to ContextVar(default=...) constructor).
        #
        # So, in case of ctx_var.get(default), the ContextVar's default is always ignored,
        # which is not what we want.
        #
        # We want to use ContextVar's default value, so we have to use get() without arguments.
        try:
            return ctx_var.get()
        except LookupError:
            return default

    return _get_ctxvar_value_or_default


# This regex matches letters and digits at the end of the string.
#
# Example matches:
#   "foo.bar.baz" => "baz"
#   "my var1" => "var1"
#   "namespace:name" => "name"
_trailing_identifier_regex = re.compile(r"[^\d\W]\w*\Z")


def _is_context_var_matching_to_param(ctx_var: contextvars.ContextVar, param_name: str) -> bool:
    found = _trailing_identifier_regex.findall(ctx_var.name)
    assert len(found) == 1
    trailing_identifier_from_context_var_name = found[0]

    return param_name == trailing_identifier_from_context_var_name


def SkipSupplyArgGetter(default):
    """Skip argument (a special marker returned by :func:`make_supply_arg_getter`).

    This is needed for cases like this::

       timezone_var = ContextVar('timezone', default='UTC')

       @supply_args(locale_var)
       def do_something_useful(user_id, locale, timezone):
           pass

    In this case, :func:`@supply_args` decorator will trigger
    :func:`make_supply_arg_getter` 3 times, like this::

       make_supply_arg_getter(timezone_var, 'user_id')
       make_supply_arg_getter(timezone_var, 'locale')
       make_supply_arg_getter(timezone_var, 'timezone')

    That happens just because in ``@supply_args(locale_var)`` there is only ``locale_var``,
    without binding to any specific parameter. So, since parameter name is not specified,
    the ``@supply_args`` decorator calls ``make_supply_arg_getter()`` for all 3 parameters.

    Obviously, we don't need to affect all 3 arguments.
    We need to somehow guess which one is matching to the ``timezone_var``.

    To resolve the issue, there is a special convention:

    :func:`make_supply_arg_getter` may return a special marker :func:`SkipSupplyArgGetter`,
    that means: "There is no match for this argument, please skip it.".

    You can use it for extending :func:`make_supply_arg_getter` for your custom types, like this::

        @make_supply_arg_getter.register
        def make_supply_arg_getter_for_my_custom_storage(
            storage: MyCustomStorage, name: str
        ) -> SupplyArgGetterFn:
            if name not in storage:
                return SkipSupplyArgGetter

            def _my_custom_storage_getter(default):
                return storage.get(name, default)

            return _my_custom_storage_getter
    """
    raise AssertionError("This getter should be ignored, and never be called.")
