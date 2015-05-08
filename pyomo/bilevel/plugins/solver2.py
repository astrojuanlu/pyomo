#  _________________________________________________________________________
#
#  Pyomo: Python Optimization Modeling Objects
#  Copyright (c) 2014 Sandia Corporation.
#  Under the terms of Contract DE-AC04-94AL85000 with Sandia Corporation,
#  the U.S. Government retains certain rights in this software.
#  This software is distributed under the BSD License.
#  _________________________________________________________________________

import time
import pyutilib.misc
import pyomo.opt
import pyomo.util
from pyomo.core import TransformationFactory


class BILEVEL_Solver2(pyomo.opt.OptSolver):

    pyomo.util.plugin.alias('bilevel_blp_global', doc='Global solver for continuous bilevel linear problems')

    def __init__(self, **kwds):
        kwds['type'] = 'bilevel_blp_global'
        pyomo.opt.OptSolver.__init__(self,**kwds)

    def _presolve(self, *args, **kwds):
        self._instance = args[0]
        pyomo.opt.OptSolver._presolve(self, *args, **kwds)

    def _apply_solver(self):
        start_time = time.time()
        #
        # Cache the instance
        #
        xfrm = TransformationFactory('bilevel.linear_mpec')
        xfrm.apply_to(self._instance)
        xfrm = TransformationFactory('mpec.simple_disjunction')
        xfrm.apply_to(self._instance)
        xfrm = TransformationFactory('gdp.bigm')
        xfrm.apply_to(self._instance, default_bigM=100000)
        #
        # Solve with a specified solver
        #
        solver = self.options.solver
        if not self.options.solver:
            solver = 'glpk'
        opt = pyomo.opt.SolverFactory(solver)
        #
        self.results = []
        self.results.append(opt.solve(self._instance, 
                                 tee=self.tee, 
                                 timelimit=self._timelimit))
        #
        # Load the result back into the original model
        #
        self._instance.load(self.results[0], ignore_invalid_labels=True)
        #
        stop_time = time.time()
        self.wall_time = stop_time - start_time
        #
        # Deactivate the block that contains the optimality conditions,
        # and reactivate SubModel
        #
        self._instance._transformation_data['bilevel.linear_mpec'].submodel_cuid.find_component(self._instance).activate()
        self._instance._transformation_data['bilevel.linear_mpec'].block_cuid.find_component(self._instance).deactivate()
        #
        # Return the sub-solver return condition value and log
        #
        return pyutilib.misc.Bunch(rc=getattr(opt,'_rc', None), log=getattr(opt,'_log',None))

    def _postsolve(self):
        #
        # Create a results object
        #
        results = pyomo.opt.SolverResults()
        #
        # SOLVER
        #
        solv = results.solver
        solv.name = self.options.subsolver
        #solv.status = self._glpk_get_solver_status()
        #solv.memory_used = "%d bytes, (%d KiB)" % (peak_mem, peak_mem/1024)
        solv.wallclock_time = self.wall_time
        cpu_ = []
        for res in self.results:
            if not getattr(res.solver, 'cpu_time', None) is None:
                cpu_.append( res.solver.cpu_time )
        if len(cpu_) > 0:
            solv.cpu_time = sum(cpu_)
        #
        # TODO: detect infeasibilities, etc
        #
        solv.termination_condition = pyomo.opt.TerminationCondition.optimal
        #
        # PROBLEM
        #
        prob = results.problem
        prob.name = self._instance.name
        prob.number_of_constraints = self._instance.statistics.number_of_constraints
        prob.number_of_variables = self._instance.statistics.number_of_variables
        prob.number_of_binary_variables = self._instance.statistics.number_of_binary_variables
        prob.number_of_integer_variables = self._instance.statistics.number_of_integer_variables
        prob.number_of_continuous_variables = self._instance.statistics.number_of_continuous_variables
        prob.number_of_objectives = self._instance.statistics.number_of_objectives
        #
        from pyomo.core import maximize
        ##if self._instance.sense == maximize:
            ##prob.sense = pyomo.opt.ProblemSense.maximize
        ##else:
            ##prob.sense = pyomo.opt.ProblemSense.minimize
        #
        # SOLUTION(S)
        #
        soln, results._symbol_map = self._instance.get_solution()
        results.solution.insert( soln )
        #
        # Uncache the instance
        #
        self._instance = None
        return results
