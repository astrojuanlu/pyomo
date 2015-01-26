#  _________________________________________________________________________
#
#  Pyomo: Python Optimization Modeling Objects
#  Copyright (c) 2014 Sandia Corporation.
#  Under the terms of Contract DE-AC04-94AL85000 with Sandia Corporation,
#  the U.S. Government retains certain rights in this software.
#  This software is distributed under the BSD License.
#  _________________________________________________________________________

import argparse
import gc
import logging
import os
import sys
import textwrap
import traceback
import types
import time
from six import itervalues, iterkeys
from pyomo.util import pyomo_api

try:
    import yaml
    yaml_available=True
except ImportError:
    yaml_available=False
try:
    import cProfile as profile
except ImportError:
    import profile
try:
    import pstats
    pstats_available=True
except ImportError:
    pstats_available=False

try:
    import IPython
    IPython_available=True
    from IPython.Shell import IPShellEmbed
except:
    IPython_available=False
else:
    ipshell = IPShellEmbed([''],
                banner = '\n# Dropping into Python interpreter',
                exit_msg = '\n# Leaving Interpreter, back to Pyomo\n')

from pyutilib.misc import Options
try:
    from pympler import muppy
    from pympler import summary
    from pympler.asizeof import *
    pympler_available = True
except ImportError:
    pympler_available = False
memory_data = Options()

import pyutilib.misc
from pyomo.util.plugin import ExtensionPoint, Plugin, implements
from pyutilib.misc import Container
from pyutilib.services import TempfileManager

from pyomo.opt import ProblemFormat
from pyomo.opt.base import SolverFactory
from pyomo.opt.parallel import SolverManagerFactory
from pyomo.core import *
from pyomo.core.base.suffix import active_import_suffix_generator
from pyomo.core.base.symbol_map import TextLabeler
import pyomo.core.base

from pyomo.repn.linear_repn import linearize_model_expressions

try:
    xrange = xrange
except:
    xrange = range


filter_excepthook=False
modelapi = {    'pyomo_create_model':IPyomoScriptCreateModel,
                'pyomo_create_dataportal':IPyomoScriptCreateDataPortal,
                'pyomo_print_model':IPyomoScriptPrintModel,
                'pyomo_modify_instance':IPyomoScriptModifyInstance,
                'pyomo_print_instance':IPyomoScriptPrintInstance,
                'pyomo_save_instance':IPyomoScriptSaveInstance,
                'pyomo_print_results':IPyomoScriptPrintResults,
                'pyomo_save_results':IPyomoScriptSaveResults,
                'pyomo_postprocess':IPyomoScriptPostprocess}


logger = logging.getLogger('pyomo.core')
start_time = 0.0


@pyomo_api(namespace='pyomo.script')
def setup_environment(data):
    """
    Setup Pyomo execution environment
    """
    #
    if not yaml_available and data.options.postsolve.results_format == 'yaml':
        raise ValueError("Configuration specifies a yaml file, but pyyaml is not installed!")
    elif data.options.postsolve.results_format is None: 
        data.options.postsolve.results_format = 'json'
    #
    global start_time
    start_time = time.time()
    if not data.options.runtime.logging == 'quiet':
        sys.stdout.write('[%8.2f] Setting up Pyomo environment\n' % 0.0)
        sys.stdout.flush()

    #
    # Disable garbage collection
    #
    if data.options.runtime.disable_gc:
        gc.disable()
    #
    # Setup management for temporary files
    #
    if not data.options.runtime.tempdir is None:
        if not os.path.exists(data.options.runtime.tempdir):
            msg =  'Directory for temporary files does not exist: %s'
            raise ValueError(msg % data.options.runtime.tempdir)
        TempfileManager.tempdir = data.options.runtime.tempdir

    #
    # Configure exception management
    #
    def pyomo_excepthook(etype,value,tb):
        """
        This exception hook gets called when debugging is on. Otherwise,
        run_command in this module is called.
        """
        global filter_excepthook
        if len(data.options.model.filename) > 0:
            name = "model " + data.options.model.filename
        else:
            name = "model"


        if filter_excepthook:
            action = "loading"
        else:
            action = "running"

        msg = "Unexpected exception (%s) while %s %s:\n" % (etype.__name__, action, name)

        #
        # This handles the case where the error is propagated by a KeyError.
        # KeyError likes to pass raw strings that don't handle newlines
        # (they translate "\n" to "\\n"), as well as tacking on single
        # quotes at either end of the error message. This undoes all that.
        #
        valueStr = str(value)
        if etype == KeyError:
            valueStr = valueStr.replace("\\n","\n")
            if valueStr[0] == valueStr[-1] and valueStr[0] in "\"'":
                valueStr = valueStr[1:-1]

        logger.error(msg+valueStr)

        tb_list = traceback.extract_tb(tb,None)
        i = 0
        if not logger.isEnabledFor(logging.DEBUG) and filter_excepthook:
            while i < len(tb_list):
                if data.options.model.filename in tb_list[i][0]:
                    break
                i += 1
            if i == len(tb_list):
                i = 0
        print("\nTraceback (most recent call last):")
        for item in tb_list[i:]:
            print("  File \""+item[0]+"\", line "+str(item[1])+", in "+item[2])
            if item[3] is not None:
                print("    "+item[3])
        sys.exit(1)
    sys.excepthook = pyomo_excepthook


@pyomo_api(namespace='pyomo.script')
def apply_preprocessing(data, parser=None):
    """
    Execute preprocessing files

    Required:
        parser: Command line parser object

    Returned:
        error: This is true if an error has occurred.
    """
    data.local = pyutilib.misc.Options()
    #
    if not data.options.runtime.logging == 'quiet':
        sys.stdout.write('[%8.2f] Applying Pyomo preprocessing actions\n' % (time.time()-start_time))
        sys.stdout.flush()
    #
    global filter_excepthook
    #
    #
    # Setup solver and model
    #
    #
    if len(data.options.model.filename) == 0:
        parser.print_help()
        data.error = True
        return data
    #
    if not data.options.preprocess is None:
        for file in data.options.preprocess:
            preprocess = pyutilib.misc.import_file(file, clear_cache=True)
    #
    for ep in ExtensionPoint(IPyomoScriptPreprocess):
        ep.apply( options=data.options )
    #
    # Verify that files exist
    #
    for file in [data.options.model.filename]+data.options.data.files.value():
        if not os.path.exists(file):
            raise IOError("File "+file+" does not exist!")
    #
    filter_excepthook=True
    data.local.usermodel = pyutilib.misc.import_file(data.options.model.filename, clear_cache=True)
    filter_excepthook=False

    usermodel_dir = dir(data.local.usermodel)
    data.local._usermodel_plugins = []
    for key in modelapi:
        if key in usermodel_dir:
            class TMP(Plugin):
                implements(modelapi[key], service=True)
                def __init__(self):
                    self.fn = getattr(data.local.usermodel, key)
                def apply(self,**kwds):
                    return self.fn(**kwds)
            tmp = TMP()
            data.local._usermodel_plugins.append( tmp )
            #print "HERE", modelapi[key], pyomo.util.plugin.interface_services[modelapi[key]]

    #print "HERE", data.options._usermodel_plugins

    if 'pyomo_preprocess' in usermodel_dir:
        if data.options.model.object_name in usermodel_dir:
            msg = "Preprocessing function 'pyomo_preprocess' defined in file" \
                  " '%s', but model is already constructed!"
            raise SystemExit(msg % data.options.model.filename)
        getattr(data.local.usermodel, 'pyomo_preprocess')( options=data.options )
    #
    return data

@pyomo_api(namespace='pyomo.script')
def create_model(data):
    """
    Create instance of Pyomo model.
    
    Return:
        model:      Model object.
        instance:   Problem instance.
        symbol_map: Symbol map created when writing model to a file.
        filename:    Filename that a model instance was written to.
    """
    #
    if not data.options.runtime.logging == 'quiet':
        sys.stdout.write('[%8.2f] Creating model\n' % (time.time()-start_time))
        sys.stdout.flush()
    #
    if (pympler_available is True) and (data.options.runtime.profile_memory >= 1):
        global memory_data
        mem_used = muppy.get_size(muppy.get_objects())
        data.local.max_memory = mem_used
        print("   Total memory = %d bytes prior to model construction" % mem_used)
    #
    # Create Model
    #
    ep = ExtensionPoint(IPyomoScriptCreateModel)
    model_name = 'model'
    if data.options.model.object_name is not None: model_name = data.options.model.object_name

    if model_name in dir(data.local.usermodel):
        if len(ep) > 0:
            msg = "Model construction function 'create_model' defined in "    \
                  "file '%s', but model is already constructed!"
            raise SystemExit(msg % data.options.model.filename)
        model = getattr(data.local.usermodel, data.options.model.object_name)

        if model is None:
            msg = "'%s' object is 'None' in module %s"
            raise SystemExit(msg % (model_name, data.options.model.filename))
            sys.exit(0)

    else:
        if len(ep) == 0:
            msg = "Neither '%s' nor 'pyomo_create_model' are available in "    \
                  'module %s'
            raise SystemExit(msg % ( model_name, data.options.model.filename ))
        elif len(ep) > 1:
            msg = 'Multiple model construction plugins have been registered!'
            raise SystemExit(msg)
        else:
            model_options = data.options.model.options.value()
            #if model_options is None:
                #model_options = []
            model = ep.service().apply( options = pyutilib.misc.Container(*data.options), model_options=pyutilib.misc.Container(*model_options) )
    #
    for ep in ExtensionPoint(IPyomoScriptPrintModel):
        ep.apply( options=data.options, model=model )
    #
    # Disable canonical repn for ASL solvers, and if the user has specified 
    # as such (in which case, we assume they know what they are doing!).
    #
    # Likely we need to change the framework so that canonical repn
    # is not assumed to be required by all solvers?
    #
    if data.options.solvers[0].solver_name.startswith('asl'):
        model.skip_canonical_repn = True
    elif data.options.model.skip_canonical_repn is True:
        model.skip_canonical_repn = True
    #
    # Create Problem Instance
    #
    ep = ExtensionPoint(IPyomoScriptCreateDataPortal)
    if len(ep) > 1:
        msg = 'Multiple model data construction plugins have been registered!'
        raise SystemExit(msg)

    if len(ep) == 1:
        modeldata = ep.service().apply( options=data.options, model=model )
    else:
        modeldata = DataPortal()

    if len(data.options.data.files) > 1:
        #
        # Load a list of *.dat files
        #
        for file in data.options.data.files:
            suffix = (file).split(".")[-1]
            if suffix != "dat":
                msg = 'When specifiying multiple data files, they must all '  \
                      'be *.dat files.  File specified: %s'
                raise SystemExit(msg % str( file ))

            modeldata.load(filename=file, model=model)

        instance = model.create(modeldata, namespaces=data.options.data.namespaces, profile_memory=data.options.runtime.profile_memory, report_timing=data.options.runtime.report_timing)

    elif len(data.options.data.files) == 1:
        #
        # Load a *.dat file or process a *.py data file
        #
        suffix = (data.options.data.files[0]).split(".")[-1].lower()
        if suffix == "dat":
            instance = model.create(data.options.data.files[0], namespaces=data.options.data.namespaces, profile_memory=data.options.runtime.profile_memory, report_timing=data.options.runtime.report_timing)
        elif suffix == "py":
            userdata = pyutilib.misc.import_file(data.options.data.files[0], clear_cache=True)
            if "modeldata" in dir(userdata):
                if len(ep) == 1:
                    msg = "Cannot apply 'pyomo_create_modeldata' and use the" \
                          " 'modeldata' object that is provided in the model"
                    raise SystemExit(msg)

                if userdata.modeldata is None:
                    msg = "'modeldata' object is 'None' in module %s"
                    raise SystemExit(msg % str( data.options.data.files[0] ))

                modeldata=userdata.modeldata

            else:
                if len(ep) == 0:
                    msg = "Neither 'modeldata' nor 'pyomo_create_dataportal' "  \
                          'is defined in module %s'
                    raise SystemExit(msg % str( data.options.data.files[0] ))

            modeldata.read(model)
            instance = model.create(modeldata, namespaces=data.options.data.namespaces, profile_memory=data.options.runtime.profile_memory, report_timing=data.options.runtime.report_timing)
        elif suffix == "yml" or suffix == 'yaml':
            try:
                import yaml
            except:
                msg = "Cannot apply load data from a YAML file: PyYaml is not installed"
                raise SystemExit(msg)

            modeldata = yaml.load(open(data.options.data.files[0]))
            instance = model.create(modeldata, namespaces=data.options.data.namespaces, profile_memory=data.options.runtime.profile_memory, report_timing=data.options.runtime.report_timing)
        else:
            raise ValueError("Unknown data file type: "+data.options.data.files[0])
    else:
        instance = model.create(modeldata, namespaces=data.options.data.namespaces, profile_memory=data.options.runtime.profile_memory, report_timing=data.options.runtime.report_timing)

    if data.options.model.linearize_expressions is True:
        linearize_model_expressions(instance)

    #
    modify_start_time = time.time()
    for ep in ExtensionPoint(IPyomoScriptModifyInstance):
        if data.options.runtime.report_timing is True:
            tick = time.time()
        ep.apply( options=data.options, model=model, instance=instance )
        if data.options.runtime.report_timing is True:
            print("      %6.2f seconds to apply %s" % (time.time() - tick, type(ep)))
            tick = time.time()
    #
    for transformation in data.options.transform:
        instance = instance.transform(transformation.value())
        if instance is None:
            raise SystemExit("Unexpected error while applying transformation '%s'" % transformation)
    #
    if data.options.runtime.report_timing is True:
        total_time = time.time() - modify_start_time
        print("      %6.2f seconds required for problem transformations" % total_time)
        
    if logger.isEnabledFor(logging.DEBUG):
        print("MODEL INSTANCE")
        instance.pprint()
        print("")

    for ep in ExtensionPoint(IPyomoScriptPrintInstance):
        ep.apply( options=data.options, instance=instance )

    fname=None
    symbol_map=None
    if not data.options.model.save is None:

        if data.options.runtime.report_timing is True:
            write_start_time = time.time()

        if data.options.model.save == True:
            if data.local.model_format in (ProblemFormat.cpxlp, ProblemFormat.lpxlp):
                fname = (data.options.data.files[0])[:-3]+'lp'
            else:
                fname = (data.options.data.files[0])[:-3]+str(data.local.model_format)
            format=data.local.model_format
        else:
            fname = data.options.model.save
            format=None
        (fname, symbol_map) = instance.write(filename=fname, format=format, io_options={"symbolic_solver_labels" : data.options.model.symbolic_solver_labels, 'file_determinism': data.options.model.file_determinism})
        if not data.options.runtime.logging == 'quiet':
            if not os.path.exists(fname):
                print("ERROR: file "+fname+" has not been created!")
            else:
                print("Model written to file '"+str(fname)+"'")

        if data.options.runtime.report_timing is True:
            total_time = time.time() - write_start_time
            print("      %6.2f seconds required to write file" % total_time)

        if (pympler_available is True) and (data.options.runtime.profile_memory >= 2):
            print("")
            print("      Summary of objects following file output")
            post_file_output_summary = summary.summarize(muppy.get_objects())
            summary.print_(post_file_output_summary, limit=100)

            print("")

    for ep in ExtensionPoint(IPyomoScriptSaveInstance):
        ep.apply( options=data.options, instance=instance )

    if (pympler_available is True) and (data.options.runtime.profile_memory >= 1):
        mem_used = muppy.get_size(muppy.get_objects())
        if mem_used > data.local.max_memory:
            data.local.max_memory = mem_used
        print("   Total memory = %d bytes following Pyomo instance creation" % mem_used)

    return pyutilib.misc.Options(
                    model=model, instance=instance,
                    symbol_map=symbol_map, filename=fname )

@pyomo_api(namespace='pyomo.script')
def apply_optimizer(data, instance=None):
    """
    Perform optimization with a concrete instance

    Required:
        instance:   Problem instance.

    Returned:
        results:    Optimization results. 
        opt:        Optimizer object.
    """
    #
    if not data.options.runtime.logging == 'quiet':
        sys.stdout.write('[%8.2f] Applying solver\n' % (time.time()-start_time))
        sys.stdout.flush()
    #
    #
    # Create Solver and Perform Optimization
    #
    solver = data.options.solvers[0].solver_name
    if solver is None:
        raise ValueError("Problem constructing solver:  no solver specified")

    opt = SolverFactory( solver, solver_io=data.options.solvers[0].io_format )
    if opt is None:
        raise ValueError("Problem constructing solver `%s`" % str(solver))

    opt.keepfiles=data.options.runtime.keep_files or data.options.postsolve.print_logfile

    from pyomo.core.base.plugin import registered_callback
    for name in registered_callback:
        opt.set_callback(name, registered_callback[name])

    opt.symbolic_solver_labels = data.options.model.symbolic_solver_labels

    if getattr(data.options.solvers[0].options, 'timelimit', 0) == 0:
        data.options.solvers[0].options.timelimit=None

    if len(data.options.solvers[0].suffixes) > 0:
        for suffix_name in data.options.solvers[0].suffixes:
            if suffix_name[0] in ['"',"'"]:
                suffix_name = suffix[1:-1]
            # Don't redeclare the suffix if it already exists
            suffix = getattr(instance,suffix_name,None)
            if suffix is None:
                setattr(instance,suffix_name,Suffix(direction=Suffix.IMPORT))
            else:
                raise ValueError("Problem declaring solver suffix %s. A component "\
                                  "with that name already exists on model %s." % (suffix_name, instance.name))

    if len(data.options.solvers[0].options) > 0:
        #print " ".join("%s=%s" % (key, value) for key, value in data.options.solvers[0].options.iteritems() if not key == 'timelimit')
        opt.set_options(" ".join("%s=%s" % (key, value) for key, value in data.options.solvers[0].options.iteritems() if not key == 'timelimit'))
    if not data.options.solvers[0].options_string is None:
        opt.set_options(data.options.solvers[0].options_string)

    if data.options.solvers[0].manager is None:
        solver_mngr = SolverManagerFactory( 'serial' )
    elif not data.options.solvers[0].manager in SolverManagerFactory.services():
        raise ValueError("Unknown solver manager %s" % data.options.solvers[0].manager)
    else:
        solver_mngr = SolverManagerFactory( data.options.solvers[0].manager )

    if solver_mngr is None:
        msg = "Problem constructing solver manager '%s'"
        raise ValueError(msg % str( data.options.solvers[0].manager ))

    results = solver_mngr.solve( instance, 
                                 opt=opt, 
                                 tee=data.options.runtime.stream_output, 
                                 timelimit=getattr(data.options.solvers[0].options, 'timelimit', 0) )

    if results == None:
        raise ValueError("opt.solve returned None")

    if (pympler_available is True) and (data.options.runtime.profile_memory >= 1):
        global memory_data
        mem_used = muppy.get_size(muppy.get_objects())
        if mem_used > data.local.max_memory:
            data.local.max_memory = mem_used
        print("   Total memory = %d bytes following optimization" % mem_used)

    return pyutilib.misc.Options(results=results, opt=opt)


@pyomo_api(namespace='pyomo.script')
def process_results(data, instance=None, results=None, opt=None):
    """
    Process optimization results.

    Required:
        instance:   Problem instance.
        results:    Optimization results object.
        opt:        Optimizer object.
    """
    #
    if not data.options.runtime.logging == 'quiet':
        sys.stdout.write('[%8.2f] Processing results\n' % (time.time()-start_time))
        sys.stdout.flush()
    #
    if data.options.postsolve.print_logfile:
        print("")
        print("==========================================================")
        print("Solver Logfile:",opt.log_file)
        print("==========================================================")
        print("")
        INPUT = open(opt.log_file, "r")
        for line in INPUT:
            print(line,)
        INPUT.close()
    #
    # JDS: FIXME: This is a HACK for the ASL.  The SOL file does not
    # actually contain the objective values, so we must calculate them
    # ourselves.  Ideally, this should be done as part of the ASL solver
    # (i.e., part of reading in a SOL file), however, given the current
    # workflow structure, the necessary information is not present
    # (i.e., results reading is supposed to be independent of the
    # instance and the symbol_map).  This should be revisited as part of
    # any workflow overhaul.
    if instance is not None and results is not None and \
           results._symbol_map is not None:
        # We need the symbol map in order to translate the strings
        # coming back in the results object to the actual varvalues in
        # the instance
        _symbolMap = results._symbol_map

        # This is a lot of work to get the flattened list of objectives
        # (especially since all the solvers are single-objective)...
        # But this is safe for multi-objective use (both multiple
        # objectives and indexed objectives)
        _objectives = []
        for obj in itervalues(instance.components(Objective)):
            _objectives.extend(obj.values())
        _nObj = len(_objectives)
        
        labeler = None
        for _result in xrange(len(results.solution)):
            _soln = results.solution[_result] 

            # The solution objective keys may have data on them (like suffixes) 
            # yet lack a value. This is still an ugly hack, but we really only
            # need to go through the rest of this process for those objectives
            # results that lack a .value attribute.
            _incomplete_objectives = []

            for obj in _objectives:
                try:
                    if (_symbolMap.getObject("__default_objective__") is obj) \
                            and ("__default_objective__" in _soln.objective):
                        _soln.objective["__default_objective__"].value
                    else:
                        if labeler is None:
                            labeler = TextLabeler()
                        lbl = _symbolMap.getSymbol(obj, labeler)
                        if lbl in _soln.objective:
                            _soln.objective[lbl].value
                        else:
                            _incomplete_objectives.append(obj)
                except AttributeError:
                    _incomplete_objectives.append(obj)

            if len(_incomplete_objectives) == 0:
                continue

            if labeler is None:
                labeler = TextLabeler()

            # Save the original instance values... that way the original
            # instance does not change "unexpectedly"
            _orig_val_map = {}

            # We need to map the symbols returned by the solver results
            # to their "official" symbols.  This is because the ASL
            # actually returns *aliases* as the names in the results
            # object <sigh>.
            _results_name_map = {}
            for var in iterkeys(_soln.variable):
                # dangerous: this assumes that all results from the solver
                # actually went through the symbol map
                _name = _symbolMap.getSymbol(_symbolMap.getObject(var), labeler)
                _results_name_map[_name] = var

            # Pull the variables out of the objective, override them
            # with the results from the solver, and evaluate each
            # objective
            for obj in _objectives:
                for var in pyomo.core.base.expr.identify_variables( obj.expr, False ):
                    # dangerous: this assumes that all variables
                    # actually went through the symbol map
                    s = results._symbol_map.getSymbol(var, labeler)
                    if s not in _orig_val_map:
                        _orig_val_map.setdefault(s, (var, var.value))
                        if s in _results_name_map:
                            var.value = _soln.variable[_results_name_map[s]]['Value']
                        else:
                            var.value = 0.0
                try:
                    obj_val = value(obj.expr)
                except NotImplementedError:
                    obj_val = 'unknown'

                if _symbolMap.getObject("__default_objective__") is obj:
                    _soln.objective["__default_objective__"].value = obj_val
                else:
                    _soln.objective[ _symbolMap.getSymbol(obj, labeler) ].value = obj_val

            # Finally, put the variables back to their original values
            for var, val in itervalues(_orig_val_map):
                var.value = val
    #
    try:
        # transform the results object into human-readable names.
        # IMPT: the resulting object won't be loadable - it's only for output.
        transformed_results = instance.update_results(results)
    except Exception:
        print("Problem updating solver results")
        raise
    #
    if not data.options.postsolve.show_results:
        if data.options.postsolve.save_results:
            results_file = data.options.postsolve.save_results
        elif data.options.postsolve.results_format == 'yaml':
            results_file = 'results.yml'
        else:
            results_file = 'results.json'
        transformed_results.write(filename=results_file, format=data.options.postsolve.results_format)
        if not data.options.runtime.logging == 'quiet':
            print("    Number of solutions: "+str(len(transformed_results.solution)))
            if len(transformed_results.solution) > 0:
                print("    Solution Information")
                print("      Gap: "+str(transformed_results.solution[0].gap))
                print("      Status: "+str(transformed_results.solution[0].status))
                if len(transformed_results.solution[0].objective) == 1:
                    key = transformed_results.solution[0].objective.keys()[0]
                    print("      Function Value: "+str(transformed_results.solution[0].objective[key].value))
            print("    Solver results file: "+results_file)
    #
    ep = ExtensionPoint(IPyomoScriptPrintResults)
    if len(ep) == 0:
        try:
            instance.load(results)
        except Exception:
            print("Problem loading solver results")
            raise
    if data.options.postsolve.show_results:
        print("")
        transformed_results.write(num=1, format=data.options.postsolve.results_format)
        print("")
    #
    if data.options.postsolve.summary:
        print("")
        print("==========================================================")
        print("Solution Summary")
        print("==========================================================")
        if len(results.solution(0).variable) > 0:
            print("")
            display(instance)
            print("")
        else:
            print("No solutions reported by solver.")
    #
    for ep in ExtensionPoint(IPyomoScriptPrintResults):
        ep.apply( options=data.options, instance=instance, results=results )
    #
    for ep in ExtensionPoint(IPyomoScriptSaveResults):
        ep.apply( options=data.options, instance=instance, results=results )
    #
    if (pympler_available is True) and (data.options.runtime.profile_memory >= 1):
        global memory_data
        mem_used = muppy.get_size(muppy.get_objects())
        if mem_used > data.local.max_memory:
            data.local.max_memory = mem_used
        print("   Total memory = %d bytes following results processing" % mem_used)

@pyomo_api(namespace='pyomo.script')
def apply_postprocessing(data, instance=None, results=None):
    """
    Apply post-processing steps.

    Required:
        instance:   Problem instance.
        results:    Optimization results object.
    """
    #
    if not data.options.runtime.logging == 'quiet':
        sys.stdout.write('[%8.2f] Applying Pyomo postprocessing actions\n' % (time.time()-start_time))
        sys.stdout.flush()
    #
    for file in data.options.postprocess:
        postprocess = pyutilib.misc.import_file(file, clear_cache=True)
        if "pyomo_postprocess" in dir(postprocess):
            postprocess.pyomo_postprocess(data.options, instance,results)
    for ep in ExtensionPoint(IPyomoScriptPostprocess):
        ep.apply( options=data.options, instance=instance, results=results )

    if (pympler_available is True) and (data.options.runtime.profile_memory >= 1):
        mem_used = muppy.get_size(muppy.get_objects())
        if mem_used > data.local.max_memory:
            data.local.max_memory = mem_used
        print("   Total memory = %d bytes upon termination" % mem_used)

@pyomo_api(namespace='pyomo.script')
def finalize(data, model=None, instance=None, results=None):
    """
    Perform final actions to finish the execution of the pyomo script.

    This function prints statistics related to the execution of the pyomo script.
    Additionally, this function will drop into the python interpreter if the `interactive` 
    option is `True`.

    Required:
        model:      A pyomo model object.

    Optional:
        instance:   A problem instance derived from the model object.
        results:    Optimization results object.
    """
    #
    # Deactivate and delete plugins
    #
    ##import gc
    ##print "HERE - usermodel_plugins"
    ##_tmp = data.options._usermodel_plugins[0]
    cleanup()
    configure_loggers(reset=True)
    data.local._usermodel_plugins = []
    ##gc.collect()
    ##print gc.get_referrers(_tmp)
    ##import pyomo.core.base.plugin
    ##print pyomo.util.plugin.interface_services[pyomo.core.base.plugin.IPyomoScriptSaveResults]
    ##print "HERE - usermodel_plugins"
    ##
    if not data.options.runtime.logging == 'quiet':
        sys.stdout.write('[%8.2f] Pyomo Finished\n' % (time.time()-start_time))
        if (pympler_available is True) and (data.options.runtime.profile_memory >= 1):
            sys.stdout.write('Maximum memory used = %d bytes\n' % data.local.max_memory)
        sys.stdout.flush()
    #
    model=model
    instance=instance
    results=results
    #
    if data.options.runtime.interactive:
        if IPython_available:
            ipshell()
        else:
            import code
            shell = code.InteractiveConsole(locals())
            print('\n# Dropping into Python interpreter')
            shell.interact()
            print('\n# Leaving Interpreter, back to Pyomo\n')


def configure_loggers(options=None, reset=False):
    if reset:
        options = Options()
        options.runtime = Options()
        options.runtime.logging = 'quiet'
        logging.getLogger('pyomo.core').handlers = []
        logging.getLogger('pyomo').handlers = []
        logging.getLogger('pyutilib').handlers = []
    #
    # Configure the logger
    #
    if options.runtime.logging == 'quiet':
        logging.getLogger('pyomo.core').setLevel(logging.ERROR)
        logging.getLogger('pyomo').setLevel(logging.ERROR)
        logging.getLogger('pyutilib').setLevel(logging.ERROR)        
    if options.runtime.logging == 'warning':
        logging.getLogger('pyomo.core').setLevel(logging.WARNING)
        logging.getLogger('pyomo').setLevel(logging.WARNING)
        logging.getLogger('pyutilib').setLevel(logging.WARNING)
    if options.runtime.logging == 'info':
        logging.getLogger('pyomo.core').setLevel(logging.INFO)
        logging.getLogger('pyomo').setLevel(logging.INFO)
        logging.getLogger('pyutilib').setLevel(logging.INFO)
    if options.runtime.logging == 'verbose':
        logger.setLevel(logging.DEBUG)
        logging.getLogger('pyomo').setLevel(logging.DEBUG)
        logging.getLogger('pyutilib').setLevel(logging.DEBUG)
    if options.runtime.logging == 'debug':
        logging.getLogger('pyomo.core').setLevel(logging.DEBUG)
        logging.getLogger('pyomo').setLevel(logging.DEBUG)
        logging.getLogger('pyutilib').setLevel(logging.DEBUG)
    if options.runtime.output:
        logging.getLogger('pyomo.core').handlers = []
        logging.getLogger('pyomo').handlers = []
        logging.getLogger('pyutilib').handlers = []
        logging.getLogger('pyomo.core').addHandler( logging.FileHandler(options.runtime.output, 'w'))
        logging.getLogger('pyomo').addHandler( logging.FileHandler(options.runtime.output, 'w'))
        logging.getLogger('pyutilib').addHandler( logging.FileHandler(options.runtime.output, 'w'))
        

@pyomo_api(namespace='pyomo.script')
def run_command(command=None, parser=None, args=None, name='unknown', data=None, options=None):
    """
    Execute a function that processes command-line arguments and
    then calls a command-line driver.

    This function provides a generic facility for executing a command
    function is rather generic.  This function is segregated from
    the driver to enable profiling of the command-line execution.

    Required:
        command:    The name of a function that will be executed to perform process the command-line
                    options with a parser object.
        parser:     The parser object that is used by the command-line function.

    Optional:
        options:    If this is not None, then ignore the args option and use
                    this to specify command options.
        args:       Command-line arguments that are parsed.  If this value is `None`, then the
                    arguments in `sys.argv` are used to parse the command-line.
        name:       Specifying the name of the command-line (for error messages).
        data:       A container of labeled data.

    Returned:
        retval:     Return values from the command-line execution.
        errorcode:  0 if Pyomo ran successfully
    """
    #
    #
    # Parse command-line options
    #
    #
    retval = None
    errorcode = 0
    if options is None:
        try:
            if type(args) is argparse.Namespace:
                _options = args
            else:
                _options = parser.parse_args(args=args)
            # Replace the parser options object with a pyutilib.misc.Options object
            options = pyutilib.misc.Options()
            for key in dir(_options):
                if key[0] != '_':
                    val = getattr(_options, key)
                    if not isinstance(val, types.MethodType):
                        options[key] = val
        except SystemExit:
            # the parser throws a system exit if "-h" is specified - catch
            # it to exit gracefully.
            return Container(retval=retval, errorcode=errorcode)
    #
    # Configure loggers
    #
    configure_loggers(options=options)
    #
    # Setup I/O redirect to a file
    #
    logfile = options.runtime.output
    if not logfile is None:
        pyutilib.misc.setup_redirect(logfile)
    #
    # Call the main Pyomo runner with profiling
    #
    TempfileManager.push()
    pcount = options.runtime.profile_count
    if pcount > 0:
        if not pstats_available:
            if not logfile is None:
                pyutilib.misc.reset_redirect()
            msg = "Cannot use the 'profile' option.  The Python 'pstats' "    \
                  'package cannot be imported!'
            raise ValueError(msg)
        tfile = TempfileManager.create_tempfile(suffix=".profile")
        tmp = profile.runctx(
          command.__name__ + '(options=options,parser=parser)', command.__globals__, locals(), tfile
        )
        p = pstats.Stats(tfile).strip_dirs()
        p.sort_stats('time', 'cumulative')
        p = p.print_stats(pcount)
        p.print_callers(pcount)
        p.print_callees(pcount)
        p = p.sort_stats('cumulative','calls')
        p.print_stats(pcount)
        p.print_callers(pcount)
        p.print_callees(pcount)
        p = p.sort_stats('calls')
        p.print_stats(pcount)
        p.print_callers(pcount)
        p.print_callees(pcount)
        retval = tmp
    else:
        #
        # Call the main Pyomo runner without profiling
        #
        TempfileManager.push()
        try:
            retval = command(options=options, parser=parser)
        except SystemExit:
            err = sys.exc_info()[1]
            #
            # If debugging is enabled or the 'catch' option is specified, then 
            # exit.  Otherwise, print an "Exiting..." message.
            #
            if __debug__ and (options.runtime.logging == 'debug' or options.runtime.catch_errors):
                sys.exit(0)
            print('Exiting %s: %s' % (name, str(err)))
            errorcode = err.code
        except Exception:
            err = sys.exc_info()[1]
            #
            # If debugging is enabled or the 'catch' option is specified, then 
            # pass the exception up the chain (to pyomo_excepthook)
            #
            if __debug__ and (options.runtime.logging == 'debug' or options.runtime.catch_errors):
                if not logfile is None:
                    pyutilib.misc.reset_redirect()
                TempfileManager.pop()
                raise

            if len(options.model.filename) > 0:
                model = "model " + options.model.filename
            else:
                model = "model"

            global filter_excepthook
            if filter_excepthook:
                action = "loading"
            else:
                action = "running"

            msg = "Unexpected exception while %s %s:\n" % (action, model)
            #
            # This handles the case where the error is propagated by a KeyError.
            # KeyError likes to pass raw strings that don't handle newlines
            # (they translate "\n" to "\\n"), as well as tacking on single
            # quotes at either end of the error message. This undoes all that.
            #
            errStr = str(err)
            if type(err) == KeyError and errStr != "None":
                errStr = str(err).replace(r"\n","\n")[1:-1]

            logging.getLogger('pyomo.core').error(msg+errStr)
            errorcode = 1

    if not logfile is None:
        pyutilib.misc.reset_redirect()

    if options.runtime.disable_gc:
        gc.enable()
    TempfileManager.pop(remove=not options.runtime.keep_files)
    return Container(retval=retval, errorcode=errorcode)


def cleanup():
    for key in modelapi:
        for ep in ExtensionPoint(modelapi[key]):
            ep.deactivate()

        
