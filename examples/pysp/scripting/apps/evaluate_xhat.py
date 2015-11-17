#  _________________________________________________________________________
#
#  Pyomo: Python Optimization Modeling Objects
#  Copyright (c) 2014 Sandia Corporation.
#  Under the terms of Contract DE-AC04-94AL85000 with Sandia Corporation,
#  the U.S. Government retains certain rights in this software.
#  This software is distributed under the BSD License.
#  _________________________________________________________________________

import sys
import time
import copy

from pyomo.core import minimize
from pyomo.pysp.util.config import (PySPConfigValue,
                                    PySPConfigBlock,
                                    safe_declare_common_option,
                                    safe_declare_unique_option,
                                    _extension_options_group_title)
from pyomo.pysp.util.misc import (parse_command_line,
                                  launch_command,
                                  sort_extensions_by_precedence)
from pyomo.pysp.scenariotree.manager_solver import \
    (ScenarioTreeManagerSolverClientSerial,
     ScenarioTreeManagerSolverClientPyro)
from pyomo.pysp.solutionioextensions import \
    (IPySPSolutionSaverExtension,
     IPySPSolutionLoaderExtension)

#
# Fix all non-anticiptative variables to their current solution,
# solve, free all variables that weren't already fixed, and
# return the extensive form objective value
#
def evaluate_current_node_solution(manager,
                                   verbose=False):

    scenario_tree = manager.scenario_tree

    # objective starts at +/- infinity and will
    # be updated if there are no solve failures
    objective_sense = manager.get_objective_sense()
    #
    # TODO: Fix this
    #
    #assert objective_sense is not None
    objective = float('inf') if (objective_sense is minimize) \
               else float('-inf')

    # Save the current fixed state and fix queue, then clear the fix queue
    fixed = {}
    fix_queue = {}
    for tree_node in scenario_tree.nodes:
        fixed[tree_node.name] = copy.deepcopy(tree_node._fixed)
        fix_queue[tree_node.name] = copy.deepcopy(tree_node._fix_queue)
        tree_node.clear_fix_queue()

    # Fix all non-anticipative variables to their
    # current value in the node solution
    for stage in scenario_tree.stages[:-1]:
        for tree_node in stage.nodes:
            for variable_id in tree_node._standard_variable_ids:
                if variable_id in tree_node._solution:
                    tree_node.fix_variable(variable_id,
                                           tree_node._solution[variable_id])
                else:
                    from pyomo.pysp.phutils import indexToString
                    name, index = tree_node._variable_ids[variable_id]
                    raise ValueError("Scenario tree variable with name %s (scenario_tree_id=%s) "
                                     "does not have a solution stored on scenario tree node %s. "
                                     "Unable to evaluate solution." % (name+indexToString(index),
                                                                       variable_id,
                                                                       tree_node.name))

    # Push fixed variable statuses on instances (or
    # transmit to the phsolverservers)
    manager.push_fix_queue_to_instances()

    failures = manager.solve_subproblems()
    if len(failures) == 0:
        objective = sum(scenario.probability * \
                        scenario.get_current_objective()
                        for scenario in scenario_tree.scenarios)

    if verbose:
        if scenario_tree.contains_bundles():
            manager.report_bundle_objectives()
        manager.report_scenario_objectives()

    # Free all non-anticipative variables
    for stage in scenario_tree._stages[:-1]:
        for tree_node in stage.nodes:
            for variable_id in tree_node._standard_variable_ids:
                tree_node.free_variable(variable_id)

    # Refix all previously fixed variables
    for tree_node in scenario_tree.nodes:
        node_fixed = fixed[tree_node.name]
        for variable_id in node_fixed:
            tree_node.fix_variable(variable_id, node_fixed[variable_id])

    manager.push_fix_queue_to_instances()

    # Restore the fix_queue
    for tree_node in scenario_tree.nodes:
        tree_node._fix_queue.update(fix_queue[tree_node.name])

    return objective

def run_evaluate_xhat_register_options(options=None):
    if options is None:
        options = PySPConfigBlock()
    safe_declare_common_option(options,
                               "disable_gc")
    safe_declare_common_option(options,
                               "profile")
    safe_declare_common_option(options,
                               "traceback")
    safe_declare_common_option(options,
                               "scenario_tree_manager")
    safe_declare_common_option(options,
                               "solution_saver_extension")
    safe_declare_common_option(options,
                               "solution_loader_extension")
    safe_declare_unique_option(
        options,
        "disable_solution_loader_check",
        PySPConfigValue(
            False,
            domain=bool,
            description=(
                "Indicates that no solution loader extension is required to "
                "run this script, e.g., because the scenario tree manager "
                "is somehow pre-populated with a solution."
            ),
            doc=None,
            visibility=0),
        ap_group=_extension_options_group_title)
    ScenarioTreeManagerSolverClientSerial.register_options(options)
    ScenarioTreeManagerSolverClientPyro.register_options(options)

    return options

#
# Convert a PySP scenario tree formulation to SMPS input files
#

def run_evaluate_xhat(options,
                      solution_loaders=(),
                      solution_savers=()):

    import pyomo.environ

    start_time = time.time()

    solution_loaders = sort_extensions_by_precedence(solution_loaders)
    solution_savers = sort_extensions_by_precedence(solution_savers)

    manager_class = None
    if options.scenario_tree_manager == 'serial':
        manager_class = ScenarioTreeManagerSolverClientSerial
    elif options.scenario_tree_manager == 'pyro':
        manager_class = ScenarioTreeManagerSolverClientPyro

    with manager_class(options) \
         as manager:
        manager.initialize()

        loaded = False
        for plugin in solution_loaders:
            ret = plugin.load(manager)
            if not ret:
                print("WARNING: Loader extension %s call did not return True. "
                      "This might indicate failure to load data." % (plugin))
            else:
                loaded = True

        if (not loaded) and (not options.disable_solution_loader_check):
            raise RuntimeError(
                "Either no solution loader extensions were provided or "
                "all solution loader extensions reported a bad return value. "
                "To disable this check use the disable_solution_loader_check "
                "option flag.")

        objective = evaluate_current_node_solution(manager,
                                                   verbose=options.verbose)
        manager.scenario_tree.snapshotSolutionFromScenarios()

        print("\nObjective=%s" % (objective))

        for plugin in solution_savers:
            if not plugin.save(manager):
                print("WARNING: Saver extension %s call did not return True. "
                      "This might indicate failure to save data." % (plugin))

    print("")
    print("Total execution time=%.2f seconds"
          % (time.time() - start_time))

    return 0

#
# the main driver routine for the evaluate_xhat script.
#

def main(args=None):
    #
    # Top-level command that executes everything
    #

    #
    # Import plugins
    #
    import pyomo.environ

    #
    # Parse command-line options.
    #
    try:
        options, extensions = parse_command_line(
            args,
            run_evaluate_xhat_register_options,
            with_extensions={'solution_loader_extension': IPySPSolutionLoaderExtension,
                             'solution_saver_extension': IPySPSolutionSaverExtension},
            prog='evaluate_xhat',
            description=(
"""Evaluates a scenario tree solution by fixing all non-anticipative
variables on the scenario tree to their current value after executing
one or more plugins implementing the IPySPSolutionLoaderExtension
interface. At a minimum, the non-leaf stage scenario tree node
solution dictionaries should be populated with values for non-derived
stage variables."""
            ))

    except SystemExit as _exc:
        # the parser throws a system exit if "-h" is specified
        # - catch it to exit gracefully.
        return _exc.code

    return launch_command(run_evaluate_xhat,
                          options,
                          cmd_kwds={'solution_loaders':
                                    extensions['solution_loader_extension'],
                                    'solution_savers':
                                    extensions['solution_saver_extension']},
                          error_label="evaluate_xhat: ",
                          disable_gc=options.disable_gc,
                          profile_count=options.profile,
                          traceback=options.traceback)

def evaluate_xhat_main(args=None):
    return main(args=args)

if __name__ == "__main__":
    main(args=sys.argv[1:])
