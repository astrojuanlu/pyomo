#  _________________________________________________________________________
#
#  Pyomo: Python Optimization Modeling Objects
#  Copyright (c) 2014 Sandia Corporation.
#  Under the terms of Contract DE-AC04-94AL85000 with Sandia Corporation,
#  the U.S. Government retains certain rights in this software.
#  This software is distributed under the BSD License.
#  _________________________________________________________________________

__all__ = ['SystemCallSolver']

import os
import sys
import time
import logging

from pyutilib.common import ApplicationError, WindowsError
from pyutilib.misc import Bunch
from pyutilib.services import registered_executable, TempfileManager
from pyutilib.subprocess import run

from pyomo.opt.base import *
from pyomo.opt.base.solvers import *
from pyomo.opt.results import SolverStatus, SolverResults

logger = logging.getLogger('pyomo.opt')


class SystemCallSolver(OptSolver):
    """ A generic command line solver """

    def __init__(self, **kwargs):
        """ Constructor """
        OptSolver.__init__(self, **kwargs)
        self.keepfiles  = kwargs.pop('keepfiles', False )
        self.soln_file  = None
        self.log_file   = None
        self._timelimit = None
        self._timer     = ''


    def available(self, exception_flag=False):
        """ True if the solver is available """
        if self._assert_available:
            return True
        if not OptSolver.available(self,exception_flag):
            return False
        ans=self.executable()
        if ans is None:
            if exception_flag:
                msg = "No executable found for solver '%s'"
                raise ApplicationError(msg % self.name)
            return False
        return True

    def create_command_line(self,executable,problem_files):
        """
        Create the command line that is executed.
        """
        raise NotImplementedError       #pragma:nocover

    def process_logfile(self):
        """
        Process the logfile for information about the optimization process.
        """
        return SolverResults()

    def process_soln_file(self,results):
        """
        Process auxilliary data files generated by the optimizer (e.g. solution
        files)
        """
        return results

    def _executable(self):
        """
        Returns the executable used by this solver.
        """
        raise NotImplementedError

    def _presolve(self, *args, **kwds):
        """
        Peform presolves.
        """
        TempfileManager.push()

        if 'keepfiles' in kwds:
            self.keepfiles = kwds['keepfiles']
            del kwds['keepfiles']

        if 'symbolic_solver_labels' in kwds:
            self.symbolic_solver_labels = kwds['symbolic_solver_labels']
            del kwds['symbolic_solver_labels']

        OptSolver._presolve(self, *args, **kwds)

        #
        # Verify that the input problems exists
        #
        for filename in self._problem_files:
            if not os.path.exists(filename):
                msg = 'Solver failed to locate input problem file: %s'
                raise ValueError(msg % filename)
        #
        # Create command line
        #
        self._command = self.create_command_line(
                              self.executable(), self._problem_files )
        self.log_file=self._command.log_file
        #
        # The pre-cleanup is probably unncessary, but also not harmful.
        #
        if self.log_file is not None and os.path.exists(self.log_file):
            os.remove(self.log_file)
        if self.soln_file is not None and os.path.exists(self.soln_file):
            os.remove(self.soln_file)


    def _apply_solver(self):
        if registered_executable('timer'):
            self._timer = registered_executable('timer').get_path()
        #
        # Execute the command
        #
        if __debug__ and logger.isEnabledFor(logging.DEBUG):
            logger.debug("Running %s", self._command.cmd)

        # display the log/solver file names prior to execution. this is useful
        # in case something crashes unexpectedly, which is not without precedent.
        if self.keepfiles:
            if self.log_file is not None:
                print("Solver log file: '%s'" % self.log_file)
            if self.soln_file is not None:
                print("Solver solution file: '%s'" % self.soln_file)
            if self._problem_files is not []:
                print("Solver problem files: %s" % str(self._problem_files))

        sys.stdout.flush()
        self._rc, self._log = self._execute_command(self._command)
        sys.stdout.flush()
        return Bunch(rc=self._rc, log=self._log)

    def _postsolve(self):

        if self.log_file is not None:
            OUTPUT=open(self.log_file,"w")
            OUTPUT.write("Solver command line: "+str(self._command.cmd)+'\n')
            OUTPUT.write("\n")
            OUTPUT.write(self._log+'\n')
            OUTPUT.close()

        # JPW: The cleanup of the problem file probably shouldn't be here, but
        #   rather in the base OptSolver class. That would require movement of
        #   the keepfiles attribute and associated cleanup logic to the base
        #   class, which I didn't feel like doing at this present time. the
        #   base class remove_files method should clean up the problem file.

        if self.log_file is not None and not os.path.exists(self.log_file):
            msg = "File '%s' not generated while executing %s"
            raise IOError(msg % ( self.log_file, self.path ))
        results = None

        if self._results_format is not None:
            results = self.process_output(self._rc)
            #
            # If keepfiles is true, then we pop the TempfileManager context while telling
            # it to _not_ remove the files.
            #
            if not self.keepfiles:
                # in some cases, the solution filename is not generated via the temp-file mechanism,
                # instead being automatically derived from the input lp/nl filename. so, we may
                # have to clean it up manually.
                if not self.soln_file is None and os.path.exists(self.soln_file):
                    os.remove(self.soln_file)

        TempfileManager.pop(remove=not self.keepfiles)

        return results

    def _execute_command(self,command):
        """
        Execute the command
        """
        try:
            if 'script' in command:
                _input = command.script
            else:
                _input = None
            [rc, log] = run(
                command.cmd,
                stdin = _input,
                timelimit = self._timelimit,
                env   = command.env,
                tee   = self.tee
             )
        except WindowsError:
            err = sys.exc_info()[1]
            msg = 'Could not execute the command: %s\tError message: %s'
            raise ApplicationError(msg % ( command.cmd, err ))
        sys.stdout.flush()
        return [rc,log]

    def process_output(self,rc):
        """
        Process the output files.
        """
        start_time = time.time()
        if self._results_format is None:
            raise ValueError("Results format is None")
        results = self.process_logfile()
        log_file_completion_time = time.time()
        if self._report_timing is True:
            print("Log file read time=%0.2f seconds" % (log_file_completion_time - start_time))
        if self.results_reader is None:
            self.process_soln_file(results)
            soln_file_completion_time = time.time()
            if self._report_timing is True:
                print("Solution file read time=%0.2f seconds" % (soln_file_completion_time - log_file_completion_time))
        else:
            # There is some ambiguity here as to where the solution data
            # It's natural to expect that the log file contains solution
            # information, but perhaps also in a results file.
            # For now, if there is a single solution, then we assume that
            # the results file is going to add more data to it.
            if len(results.solution) == 1:
                results = self.results_reader(self.results_file, res=results, soln=results.solution(0), suffixes=self.suffixes)
            else:
                results = self.results_reader(self.results_file, res=results, suffixes=self.suffixes)
            results_reader_completion_time = time.time()
            if self._report_timing is True:
                print("Results reader time=%0.2f seconds" % (results_reader_completion_time - log_file_completion_time))
        if rc != None:
            results.solver.error_rc=rc
            if rc != 0:
                results.solver.status=SolverStatus.error
        return results

    def _default_results_format(self, prob_format):
        """ Returns the default results format for different problem
            formats.
        """
        return ResultsFormat.soln
