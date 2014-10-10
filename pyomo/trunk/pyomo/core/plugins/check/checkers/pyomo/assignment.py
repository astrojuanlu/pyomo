import ast

from coopr.pyomo.plugins.check.checker import IterativeTreeChecker
from coopr.pyomo.plugins.check.model import ModelTrackerHook


class ArrayValue(IterativeTreeChecker):

    ModelTrackerHook()

    varArrays = {}

    def checkerDoc(self):
        return """\
        Assigning a value to an array of variables does nothing.
        """

    def checkVarArray(self, script, node):
        """Check for the creation of a new VarArray; store name if created"""

        if isinstance(node.value, ast.Call):
            if isinstance(node.value.func, ast.Name):
                if node.value.func.id == 'Var':
                    if len(node.value.args) > 0:
                        for target in node.targets:
                            if isinstance(target, ast.Attribute):
                                if isinstance(target.value, ast.Name):
                                    if target.value.id in script.modelVars:
                                        if target.value.id not in self.varArrays:
                                            self.varArrays[target.value.id] = []
                                        self.varArrays[target.value.id].append(target.attr)

    def checkArrayValue(self, script, node):
        for target in node.targets:
            if isinstance(target, ast.Attribute):
                if isinstance(target.value, ast.Attribute):
                    if isinstance(target.value.value, ast.Name):
                        if target.value.value.id in script.modelVars:
                            if target.value.value.id in self.varArrays:
                                if target.value.attr in self.varArrays[target.value.value.id]:
                                    if target.attr == 'value':
                                        self.problem("Assigning value to variable array {0}.{1}".format(target.value.value.id, target.value.attr), lineno = node.lineno)

    def check(self, runner, script, info):
        if isinstance(info, ast.Assign):
            self.checkVarArray(script, info)
            self.checkArrayValue(script, info)
