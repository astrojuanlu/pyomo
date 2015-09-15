#  _________________________________________________________________________
#
#  Pyomo: Python Optimization Modeling Objects
#  Copyright (c) 2014 Sandia Corporation.
#  Under the terms of Contract DE-AC04-94AL85000 with Sandia Corporation,
#  the U.S. Government retains certain rights in this software.
#  This software is distributed under the BSD License.
#  _________________________________________________________________________

__all__ = ("launch_command", "load_external_module")

import sys
import traceback
import inspect
import argparse
# for profiling
try:
    import cProfile as profile
except ImportError:
    import profile
try:
    import pstats
    pstats_available=True
except ImportError:
    pstats_available=False

from pyutilib.misc import PauseGC, import_file
from pyutilib.misc.config import ConfigBlock
from pyutilib.services import TempfileManager
import pyutilib.common
from pyomo.opt.base import ConverterError
from pyomo.util.plugin import (ExtensionPoint,
                               SingletonPlugin)
from pyomo.pysp.util.configured_object import PySPConfiguredObject

def _generate_unique_module_name():
    import uuid
    name = str(uuid.uuid4())
    while name in sys.modules:
        name = str(uuid.uuid4())
    return name

def load_external_module(module_name, unique=False, clear_cache=False):

    try:
        # make sure "." is in the PATH.
        original_path = list(sys.path)
        sys.path.insert(0,'.')

        sys_modules_key = None
        module_to_find = None
        if module_name in sys.modules:
            sys_modules_key = module_name
            if clear_cache:
                if unique:
                    sys_modules_key = _generate_unique_module_name()
                    print("Module="+module_name+" is already imported - "
                          "forcing re-import using unique module id="
                          +str(sys_modules_key))
                    module_to_find = import_file(module_name, name=sys_modules_key)
                    print("Module successfully loaded")
                else:
                    print("Module="+module_name+" is already imported - "
                          "forcing re-import")
                    module_to_find = import_file(module_name, clear_cache=True)
                    print("Module successfully loaded")
            else:
                print("Module="+module_name+" is already imported - skipping")
                module_to_find = sys.modules[module_name]
        else:
            if unique:
                sys_modules_key = _generate_unique_module_name()
                print("Importing module="+module_name+" using "
                      "unique module id="+str(sys_modules_key))
                module_to_find = import_file(module_name, name=sys_modules_key)
                print("Module successfully loaded")
            else:
                print("Importing module="+module_name)
                _context = {}
                module_to_find = import_file(module_name, context=_context)
                assert len(_context) == 1
                sys_modules_key = list(_context.keys())[0]
                print("Module successfully loaded")

    finally:
        # restore to what it was
        sys.path[:] = original_path

    return module_to_find, sys_modules_key

def sort_extensions_by_precedence(extensions):
    import pyomo.pysp.util.configured_object
    return tuple(sorted(
        extensions,
        key=lambda ext:
        (ext.get_option('extension_precedence') if \
         isinstance(ext, pyomo.pysp.util.configured_object.\
                    PySPConfiguredExtension) else \
         float('-inf'))))

def load_extensions(names, ep_type):
    import pyomo.environ

    plugins = ExtensionPoint(ep_type)

    active_plugins = []
    for this_extension in names:
        module, _ = load_external_module(this_extension)
        assert module is not None

        for name, obj in inspect.getmembers(module, inspect.isclass):
            # the second condition gets around goofyness related
            # to issubclass returning True when the obj is the
            # same as the test class.
            if issubclass(obj, SingletonPlugin) and \
               (name != "SingletonPlugin"):
                for plugin in plugins:
                    if isinstance(plugin, obj):
                        active_plugins.append(plugin)

    return tuple(active_plugins)

#
# A utility function for generating an argparse object and parsing the
# command line from a callback that registers options onto a
# ConfigBlock.  Optionally, a list of extension point types can be
# supplied, which causes reparsing to occur when any extensions are
# specified on the command-line that might register additional
# options.
#
# with_extensions: should be a dictionary mapping registered
#                  option name to the ExtensionPoint service
#

def parse_command_line(args,
                       register_options_callback,
                       with_extensions=None,
                       **kwds):

    from pyomo.pysp.util.config import _domain_tuple_of_str

    def _get_argument_parser(options):
        ap = argparse.ArgumentParser(add_help=False, **kwds)
        options.initialize_argparse(ap)
        ap.add_argument("-h", "--help", dest="show_help",
                        action="store_true", default=False,
                        help="show this help message and exit")
        return ap

    #
    # Register options
    #
    options = ConfigBlock()
    register_options_callback(options)

    if with_extensions is not None:
        for name in with_extensions:
            configval = options.get(name, None)
            assert configval is not None
            assert configval._domain is _domain_tuple_of_str

    ap = _get_argument_parser(options)
    # First parse known args, then import any extension plugins
    # specified by the user, regenerate the options block and
    # reparse to pick up plugin specific registered options
    opts, _ = ap.parse_known_args(args=args)
    options.import_argparse(opts)
    extensions = {}
    if with_extensions is None:
        if opts.show_help:
            pass
    else:
        if all(len(options.get(name).value()) == 0
               for name in with_extensions) and \
               opts.show_help:
            ap.print_help()
            sys.exit(0)
        for name in with_extensions:
            extensions[name] = load_extensions(
                options.get(name).value(),
                with_extensions[name])

    # regenerate the options
    options = ConfigBlock()
    register_options_callback(options)
    for name in extensions:
        for plugin in extensions[name]:
            if isinstance(plugin, PySPConfiguredObject):
                plugin.register_options(options)
        # do a dummy access to option to prevent
        # a warning about it not being used
        options.get(name).value()

    ap = _get_argument_parser(options)
    opts = ap.parse_args(args=args)
    options.import_argparse(opts)
    for name in extensions:
        for plugin in extensions[name]:
            if isinstance(plugin, PySPConfiguredObject):
                plugin.set_options(options)
    if opts.show_help:
        ap.print_help()
        sys.exit(0)

    if with_extensions:
        for name in extensions:
            extensions[name] = sort_extensions_by_precedence(extensions[name])
        return options, extensions
    else:
        return options

#
# When we create official command-line applications
# there is a long list of processing related to
# traceback and profile handling that should not need
# to be copy-pasted everywhere
#
def launch_command(command,
                   options,
                   cmd_args=None,
                   cmd_kwds=None,
                   error_label="",
                   disable_gc=False,
                   profile_count=0,
                   traceback=False):
    if cmd_args is None:
        cmd_args = ()
    if cmd_kwds is None:
        cmd_kwds = {}

    #
    # Control the garbage collector - more critical than I would like
    # at the moment.
    #
    with PauseGC(disable_gc) as pgc:

        #
        # Run command - precise invocation depends on whether we want
        # profiling output, traceback, etc.
        #

        rc = 0

        if pstats_available and (profile_count > 0):
            #
            # Call the main routine with profiling.
            #
            tfile = TempfileManager.create_tempfile(suffix=".profile")
            tmp = profile.runctx('command(options, *cmd_args, **cmd_kwds)',
                                 globals(),
                                 locals(),
                                 tfile)
            p = pstats.Stats(tfile).strip_dirs()
            p.sort_stats('time', 'cumulative')
            p = p.print_stats(profile_count)
            p.print_callers(profile_count)
            p.print_callees(profile_count)
            p = p.sort_stats('cumulative','calls')
            p.print_stats(profile_count)
            p.print_callers(profile_count)
            p.print_callees(profile_count)
            p = p.sort_stats('calls')
            p.print_stats(profile_count)
            p.print_callers(profile_count)
            p.print_callees(profile_count)
            TempfileManager.clear_tempfiles()
            rc = tmp
        else:

            #
            # Call the main PH routine without profiling.
            #
            if traceback:
                rc = command(options, *cmd_args, **cmd_kwds)
            else:
                try:
                    try:
                        rc = command(options, *cmd_args, **cmd_kwds)
                    except ValueError:
                        sys.stderr.write(error_label+"VALUE ERROR:\n")
                        sys.stderr.write(str(sys.exc_info()[1])+"\n")
                        raise
                    except KeyError:
                        sys.stderr.write(error_label+"KEY ERROR:\n")
                        sys.stderr.write(str(sys.exc_info()[1])+"\n")
                        raise
                    except TypeError:
                        sys.stderr.write(error_label+"TYPE ERROR:\n")
                        sys.stderr.write(str(sys.exc_info()[1])+"\n")
                        raise
                    except NameError:
                        sys.stderr.write(error_label+"NAME ERROR:\n")
                        sys.stderr.write(str(sys.exc_info()[1])+"\n")
                        raise
                    except IOError:
                        sys.stderr.write(error_label+"IO ERROR:\n")
                        sys.stderr.write(str(sys.exc_info()[1])+"\n")
                        raise
                    except ConverterError:
                        sys.stderr.write(error_label+"CONVERTER ERROR:\n")
                        sys.stderr.write(str(sys.exc_info()[1])+"\n")
                        raise
                    except pyutilib.common.ApplicationError:
                        sys.stderr.write(error_label+"APPLICATION ERROR:\n")
                        sys.stderr.write(str(sys.exc_info()[1])+"\n")
                        raise
                    except RuntimeError:
                        sys.stderr.write(error_label+"RUN-TIME ERROR:\n")
                        sys.stderr.write(str(sys.exc_info()[1])+"\n")
                        raise
                    except:
                        sys.stderr.write(error_label+
                                         "Encountered unhandled exception:\n")
                        if len(sys.exc_info()) > 1:
                            sys.stderr.write(str(sys.exc_info()[1])+"\n")
                        else:
                            traceback.print_exc(file=sys.stderr)
                        raise
                except:
                    sys.stderr.write("\n")
                    sys.stderr.write(
                        "To obtain further information regarding the "
                        "source of the exception, use the "
                        "--traceback option\n")
                    rc = 1

    #
    # TODO: Once we incorporate options registration into
    #       all of the PySP commands we will assume the
    #       options object is always a ConfigBlock
    #
    if isinstance(options, ConfigBlock):
        ignored_options = dict((_c._name, _c.value(False))
                              for _c in options.unused_user_values())
        if len(ignored_options):
            print("")
            print("*** WARNING: The following options were "
                  "explicitly set but never accessed during "
                  "execution of this command:")
            for name in ignored_options:
                print(" - %s: %s" % (name, ignored_options[name]))
            print("*** If you believe this is a bug, please report it "
                  "to the PySP developers.")
            print("")

    return rc