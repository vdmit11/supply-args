Introduction
============

The idea behind the ``@supply_args`` decorator is simple: default arguments on steroids.

In Python, you can have default values for function parameters, but they has to be constants.

The problem is that sometimes you don't want constants.

You want the default value to be, for example, taken from current user's settings.
Or, maybe you want ``datetime.utcnow()`` as a default value for a function.

In this case, you usually do a bunch of ``if`` checks, as shown below:

.. code-block::

   >>> from datetime import datetime

   >>> current_settings = {}

   >>> def print_time(dt=None, time_format=None):
   ...     if not dt:
   ...         dt = datetime.utcnow()
   ...     if not time_format:
   ...         time_format = current_settings['time_format']
   ...
   ...     print(dt.strftime(time_format))

   >>> current_settings['time_format'] = '%H:%M:%S'
   >>> print_time(datetime(2020, 1, 1, 12, 34, 56))
   12:34:56


The ``@supply_args`` decorator allows to eliminate ``if not arg`` checks,
and shorten the code, like this:

.. code-block::

   >>> from datetime import datetime
   >>> from supply_args import supply_args

   >>> current_settings = {}

   >>> @supply_args(dt=datetime.utcnow, time_format=current_settings)
   ... def print_time(dt=None, time_format=None):
   ...     print(dt.strftime(time_format))

   >>> current_settings['time_format'] = '%H:%M:%S'
   >>> print_time(datetime(2020, 1, 1, 12, 34, 56))
   12:34:56

so these lines were eliminated::

   if not dt:
     dt = datetime.utcnow()
   if not format:
     format = current_settings['time_format']

So no rocket science here. It just does one thing well: helps you to write slightly less code.

\...and it can distinguish between "argument is None" and "argument is not passed" cases.

\...and it is customizable.
The rest of the documentation is actually dedicated to how you configure and extend it.
