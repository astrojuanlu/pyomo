#  _________________________________________________________________________
#
#  Pyomo: Python Optimization Modeling Objects
#  Copyright (c) 2014 Sandia Corporation.
#  Under the terms of Contract DE-AC04-94AL85000 with Sandia Corporation,
#  the U.S. Government retains certain rights in this software.
#  This software is distributed under the BSD License.
#  _________________________________________________________________________

import ast

from pyomo.util.plugin import *
from pyomo.checker.hooks import *


class FunctionTrackerHook(SingletonPlugin):

    implements(IPreCheckHook)

    def precheck(self, runner, script, info):
        # create models dict if nonexistent
        if getattr(script, 'functionDefs', None) is None:
            script.functionDefs = {}

        # add new function definitions
        if isinstance(info, ast.FunctionDef):
            script.functionDefs[info.name] = info

        # update function def dictionary with assignments
        elif isinstance(info, ast.Assign):
            if isinstance(info.value, ast.Name):
                if info.value.id in script.functionDefs:
                    for target in info.targets:
                        if isinstance(target, ast.Name):
                            script.functionDefs[target.id] = script.functionDefs[info.value.id]
            else:
                for target in info.targets:
                    if isinstance(target, ast.Name):
                        if target.id in script.functionDefs:
                            del script.functionDefs[target.id]
