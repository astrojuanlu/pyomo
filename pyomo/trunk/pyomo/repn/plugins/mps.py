#  _________________________________________________________________________
#
#  Pyomo: Python Optimization Modeling Objects
#  Copyright (c) 2014 Sandia Corporation.
#  Under the terms of Contract DE-AC04-94AL85000 with Sandia Corporation,
#  the U.S. Government retains certain rights in this software.
#  This software is distributed under the BSD License.
#  _________________________________________________________________________

#
# Problem Writer for (Free) MPS Format Files
#

import logging
import math
import operator

from six import iteritems, StringIO
from six.moves import xrange

from pyutilib.math import infinity
from pyutilib.misc import PauseGC
import pyomo.util.plugin
from pyomo.opt import ProblemFormat
from pyomo.opt.base import AbstractProblemWriter
from pyomo.core.base import \
    (SymbolMap, TextLabeler,
     NumericLabeler, Constraint, SortComponents,
     Var, value,
     SOSConstraint, Objective,
     ComponentMap, is_fixed)
from pyomo.repn import (generate_canonical_repn,
                        canonical_degree,
                        LinearCanonicalRepn)

logger = logging.getLogger('pyomo.core')

class ProblemWriter_mps(AbstractProblemWriter):

    pyomo.util.plugin.alias('mps', 'Generate the corresponding MPS file')

    def __init__(self):

        AbstractProblemWriter.__init__(self, ProblemFormat.mps)

        # the MPS writer is responsible for tracking which variables are
        # referenced in constraints, so that one doesn't end up with a
        # zillion "unreferenced variables" warning messages. stored at
        # the object level to avoid additional method arguments.
        # dictionary of id(_VarData)->_VarData.
        self._referenced_variable_ids = {}

        # Keven Hunter made a nice point about using %.16g in his attachment
        # to ticket #4319. I am adjusting this to %.17g as this mocks the
        # behavior of using %r (i.e., float('%r'%<number>) == <number>) with
        # the added benefit of outputting (+/-). The only case where this
        # fails to mock the behavior of %r is for large (long) integers (L),
        # which is a rare case to run into and is probably indicative of
        # other issues with the model.
        # *** NOTE ***: If you use 'r' or 's' here, it will break code that
        #               relies on using '%+' before the formatting character
        #               and you will need to go add extra logic to output
        #               the number's sign.
        self._precision_string = '.17g'

    def __call__(self,
                 model,
                 output_filename,
                 solver_capability,
                 io_options):

        # Make sure not to modify the user's dictionary,
        # they may be reusing it outside of this call
        io_options = dict(io_options)

        # Skip writing constraints whose body section is
        # fixed (i.e., no variables)
        skip_trivial_constraints = \
            io_options.pop("skip_trivial_constraints", False)

        # Use full Pyomo component names in the MPS file rather
        # than shortened symbols (slower, but useful for debugging).
        symbolic_solver_labels = \
            io_options.pop("symbolic_solver_labels", False)

        output_fixed_variable_bounds = \
            io_options.pop("output_fixed_variable_bounds", False)

        # If False, unused variables will not be included in
        # the MPS file. Otherwise, include all variables in
        # the bounds sections.
        include_all_variable_bounds = \
            io_options.pop("include_all_variable_bounds", False)

        labeler = io_options.pop("labeler", None)

        # How much effort do we want to put into ensuring the
        # MPS file is written deterministically for a Pyomo model:
        #    0 : None
        #    1 : sort keys of indexed components (default)
        #    2 : sort keys AND sort names (over declaration order)
        file_determinism = io_options.pop("file_determinism", 1)

        # user defined orderings for variable and constraint
        # output
        row_order = io_options.pop("row_order", None)
        column_order = io_options.pop("column_order", None)

        # make sure the ONE_VAR_CONSTANT variable appears in
        # the objective even if the constant part of the
        # objective is zero
        force_objective_constant = \
            io_options.pop("force_objective_constant", False)

        if len(io_options):
            raise ValueError(
                "ProblemWriter_mps passed unrecognized io_options:\n\t" +
                "\n\t".join("%s = %s" % (k,v) for k,v in iteritems(io_options)))

        if symbolic_solver_labels and (labeler is not None):
            raise ValueError("ProblemWriter_mps: Using both the "
                             "'symbolic_solver_labels' and 'labeler' "
                             "I/O options is forbidden")

        if symbolic_solver_labels:
            labeler = TextLabeler()
        elif labeler is None:
            labeler = NumericLabeler('x')

        # clear the collection of referenced variables.
        self._referenced_variable_ids.clear()

        if output_filename is None:
            output_filename = model.name + ".mps"

        # when sorting, there are a non-trivial number of
        # temporary objects created. these all yield
        # non-circular references, so disable GC - the
        # overhead is non-trivial, and because references
        # are non-circular, everything will be collected
        # immediately anyway.
        with PauseGC() as pgc:
            with open(output_filename, "w") as output_file:
                symbol_map = self._print_model_MPS(
                    model,
                    output_file,
                    solver_capability,
                    labeler,
                    output_fixed_variable_bounds=output_fixed_variable_bounds,
                    file_determinism=file_determinism,
                    row_order=row_order,
                    column_order=column_order,
                    skip_trivial_constraints=skip_trivial_constraints,
                    force_objective_constant=force_objective_constant,
                    include_all_variable_bounds=include_all_variable_bounds)

        self._referenced_variable_ids.clear()

        return output_filename, symbol_map

    def _get_bound(self, exp):
        if exp is None:
            return None
        if is_fixed(exp):
            return value(exp)
        raise ValueError("non-fixed bound: " + str(exp))

    def _extract_variable_coefficients(
            self,
            row_label,
            canonical_repn,
            column_data,
            variable_to_column):

        #
        # Linear
        #
        assert isinstance(canonical_repn, LinearCanonicalRepn)
        coefficients = canonical_repn.linear
        if coefficients is not None:
            variables = canonical_repn.variables

            # the 99% case is when the input instance is a linear
            # canonical expression, so the exception should be rare.
            for vardata, coef in zip(variables, coefficients):
                self._referenced_variable_ids[id(vardata)] = vardata
                column_data[variable_to_column[vardata]].append(
                    (row_label, coef))

        #
        # Return the constant
        #
        constant = canonical_repn.constant
        if constant is None:
            constant = 0.0

        return constant

    def _printSOS(self,
                  symbol_map,
                  labeler,
                  variable_symbol_map,
                  soscondata,
                  output_file):
        if soscondata.num_variables() == 0:
            return
        output_file.write("SOS\n")

        sos_items = list(soscondata.get_items())
        level = soscondata.level

        output_file.write(" S%d SET %s\n"
                          % (level,
                             symbol_map.getSymbol(soscondata,labeler)))

        sos_template_string = "    SET %s %"+self._precision_string+"\n"
        for vardata, weight in sos_items:
            if vardata.fixed:
                raise RuntimeError(
                    "SOSConstraint '%s' includes a fixed variable '%s'. This is "
                    "currently not supported. Deactive this constraint in order to "
                    "proceed." % (soscondata.cname(True), vardata.cname(True)))
            self._referenced_variable_ids[id(vardata)] = vardata
            output_file.write(sos_template_string
                              % (variable_symbol_map.getSymbol(vardata), weight))

    def _print_model_MPS(self,
                         model,
                         output_file,
                         solver_capability,
                         labeler,
                         output_fixed_variable_bounds=False,
                         file_determinism=1,
                         row_order=None,
                         column_order=None,
                         skip_trivial_constraints=False,
                         force_objective_constant=False,
                         include_all_variable_bounds=False):

        symbol_map = SymbolMap()
        variable_symbol_map = SymbolMap()
        # NOTE: we use createSymbol instead of getSymbol because we
        #       know whether or not the symbol exists, and don't want
        #       to the overhead of error/duplicate checking.
        # cache frequently called functions
        extract_variable_coefficients = self._extract_variable_coefficients
        create_symbol_func = SymbolMap.createSymbol
        create_symbols_func = SymbolMap.createSymbols
        alias_symbol_func = SymbolMap.alias
        variable_label_pairs = []

        sortOrder = SortComponents.unsorted
        if file_determinism >= 1:
            sortOrder = sortOrder | SortComponents.indices
            if file_determinism >= 2:
                sortOrder = sortOrder | SortComponents.alphabetical

        #
        # Create variable symbols (and cache the block list)
        #
        all_blocks = []
        variable_list = []
        for block in model.block_data_objects(active=True,
                                              sort=sortOrder):

            all_blocks.append(block)

            for vardata in block.component_data_objects(
                    Var,
                    active=True,
                    sort=sortOrder,
                    descend_into=False):

                variable_list.append(vardata)
                variable_label_pairs.append(
                    (vardata,create_symbol_func(symbol_map,
                                                vardata,
                                                labeler)))

        variable_symbol_map.addSymbols(variable_label_pairs)

        # and extract the information we'll need for rapid labeling.
        object_symbol_dictionary = symbol_map.byObject
        variable_symbol_dictionary = variable_symbol_map.byObject

        # sort the variable ordering by the user
        # column_order ComponentMap
        if column_order is not None:
            variable_list.sort(key=lambda _x: column_order[_x])

        # prepare to hold the sparse columns
        variable_to_column = ComponentMap(
            (vardata, i) for i, vardata in enumerate(variable_list))
        # add one position for ONE_VAR_CONSTANT
        column_data = [[] for i in xrange(len(variable_list)+1)]
        # constraint rhs
        rhs_data = []

        # print the model name and the source, so we know
        # roughly where
        output_file.write("* Source: Pyomo MPS Writer\n")
        output_file.write("NAME %s\n" % (model.name,))

        #
        # ROWS section
        #

        numObj = 0
        onames = []
        for block in all_blocks:

            gen_obj_canonical_repn = \
                getattr(block, "_gen_obj_canonical_repn", True)

            # Get/Create the ComponentMap for the repn
            if not hasattr(block,'_canonical_repn'):
                block._canonical_repn = ComponentMap()
            block_canonical_repn = block._canonical_repn

            for objective_data in block.component_data_objects(
                    Objective,
                    active=True,
                    sort=sortOrder,
                    descend_into=False):

                numObj += 1
                onames.append(objective_data.cname())
                if numObj > 1:
                    raise ValueError(
                        "More than one active objective defined for input "
                        "model '%s'; Cannot write legal MPS file\n"
                        "Objectives: %s" % (model.cname(True), ' '.join(onames)))

                obj_label = create_symbol_func(symbol_map,
                                               objective_data,
                                               labeler)

                symbol_map.alias(objective_data, '__default_objective__')
                output_file.write("OBJSENSE\n")
                if objective_data.is_minimizing():
                    output_file.write(" MIN\n")
                else:
                    output_file.write(" MAX\n")
                output_file.write("OBJNAME\n")
                output_file.write(" %s\n" % (obj_label))
                output_file.write("ROWS\n")
                output_file.write(" N  %s\n" % (obj_label))

                if gen_obj_canonical_repn:
                    canonical_repn = \
                        generate_canonical_repn(objective_data.expr)
                    block_canonical_repn[objective_data] = canonical_repn
                else:
                    canonical_repn = block_canonical_repn[objective_data]

                degree = canonical_degree(canonical_repn)
                if degree == 0:
                    print("Warning: Constant objective detected, replacing "
                          "with a placeholder to prevent solver failure.")
                    force_objective_constant = True
                elif degree == 2:
                    raise RuntimeError(
                        "Cannot write legal MPS file. Objective '%s' "
                        "has quadratic terms." % objective_data.cname(True))
                elif degree != 1:
                    raise RuntimeError(
                        "Cannot write legal MPS file. Objective '%s' "
                        "has nonlinear terms." % objective_data.cname(True))

                constant = extract_variable_coefficients(
                    obj_label,
                    canonical_repn,
                    column_data,
                    variable_to_column)
                if force_objective_constant or (constant != 0.0):
                    # ONE_VAR_CONSTANT
                    column_data[-1].append((obj_label, constant))

        if numObj == 0:
            raise ValueError(
                "Cannot write legal MPS file: No objective defined "
                "for input model '%s'." % str(model))

        # Constraints
        def constraint_generator():
            for block in all_blocks:

                gen_con_canonical_repn = \
                    getattr(block, "_gen_con_canonical_repn", True)

                # Get/Create the ComponentMap for the repn
                if not hasattr(block,'_canonical_repn'):
                    block._canonical_repn = ComponentMap()
                block_canonical_repn = block._canonical_repn

                for constraint_data in block.component_data_objects(
                        Constraint,
                        active=True,
                        sort=sortOrder,
                        descend_into=False):

                    if isinstance(constraint_data, LinearCanonicalRepn):
                        canonical_repn = constraint_data
                    else:
                        if gen_con_canonical_repn:
                            canonical_repn = generate_canonical_repn(
                                constraint_data.body)
                            block_canonical_repn[constraint_data] = canonical_repn
                        else:
                            canonical_repn = block_canonical_repn[constraint_data]

                    yield constraint_data, canonical_repn

        if row_order is not None:
            sorted_constraint_list = list(constraint_generator())
            sorted_constraint_list.sort(key=lambda x: row_order[x[0]])
            def yield_all_constraints():
                for constraint_data, canonical_repn in sorted_constraint_list:
                    yield constraint_data, canonical_repn
        else:
            yield_all_constraints = constraint_generator

        for constraint_data, canonical_repn in yield_all_constraints():

            degree = canonical_degree(canonical_repn)

            # Write constraint
            if degree == 0:
                if skip_trivial_constraints:
                    continue
            elif degree == 2:
                raise RuntimeError(
                    "Cannot write legal MPS file. Constraint '%s' "
                    "has quadratic terms." % constraint_data.cname(True))
            elif degree != 1:
                raise RuntimeError(
                    "Cannot write legal MPS file. Constraint '%s' "
                    "has nonlinear terms." % constraint_data.cname(True))

            # Create symbol
            con_symbol = create_symbol_func(symbol_map,
                                            constraint_data,
                                            labeler)

            if constraint_data.equality:
                label = 'c_e_' + con_symbol + '_'
                alias_symbol_func(symbol_map, constraint_data, label)
                output_file.write(" E  %s\n" % (label))
                offset = extract_variable_coefficients(
                    label,
                    canonical_repn,
                    column_data,
                    variable_to_column)
                bound = constraint_data.lower
                bound = self._get_bound(bound) - offset
                rhs_data.append((label, bound))
            else:
                if constraint_data.lower is not None:
                    if constraint_data.upper is not None:
                        label = 'r_l_' + con_symbol + '_'
                    else:
                        label = 'c_l_' + con_symbol + '_'
                    alias_symbol_func(symbol_map, constraint_data, label)
                    output_file.write(" G  %s\n" % (label))
                    offset = extract_variable_coefficients(
                        label,
                        canonical_repn,
                        column_data,
                        variable_to_column)
                    bound = constraint_data.lower
                    bound = self._get_bound(bound) - offset
                    rhs_data.append((label, bound))
                if constraint_data.upper is not None:
                    if constraint_data.lower is not None:
                        label = 'r_u_' + con_symbol + '_'
                    else:
                        label = 'c_u_' + con_symbol + '_'
                    alias_symbol_func(symbol_map, constraint_data, label)
                    output_file.write(" L  %s\n" % (label))
                    offset = extract_variable_coefficients(
                        label,
                        canonical_repn,
                        column_data,
                        variable_to_column)
                    bound = constraint_data.upper
                    bound = self._get_bound(bound) - offset
                    rhs_data.append((label, bound))

        if len(column_data[-1]) > 0:
            # ONE_VAR_CONSTANT = 1
            output_file.write(" E  c_e_ONE_VAR_CONSTANT\n")
            column_data[-1].append(("c_e_ONE_VAR_CONSTANT",1))
            rhs_data.append(("c_e_ONE_VAR_CONSTANT",1))

        entry_template = "%s %"+self._precision_string
        #
        # COLUMNS section
        #
        output_file.write("COLUMNS\n")
        cnt = 0
        for vardata in variable_list:
            col_entries = column_data[variable_to_column[vardata]]
            cnt += 1
            # Don't output empty columns
            if len(col_entries):
                var_label = variable_symbol_dictionary[id(vardata)]
                for i, (row_label, coef) in enumerate(col_entries):
                    if (i%2) == 0:
                        output_file.write("    %s "
                                          % (var_label))
                        output_file.write(entry_template
                                          % (row_label, coef))
                    else:
                        output_file.write((" "+entry_template+"\n")
                                          % (row_label, coef))
                if (i%2) == 0:
                    output_file.write("\n")
        assert cnt == len(column_data)-1
        if len(column_data[-1]) > 0:
            col_entries = column_data[-1]
            var_label = "ONE_VAR_CONSTANT"
            for i, (row_label, coef) in enumerate(col_entries):
                if (i%2) == 0:
                    output_file.write("    %s "
                                      % (var_label))
                    output_file.write(entry_template
                                      % (row_label, coef))
                else:
                    output_file.write((" "+entry_template+"\n")
                                      % (row_label, coef))
            if (i%2) == 0:
                output_file.write("\n")

        #
        # RHS section
        #
        output_file.write("RHS\n")
        for i, (row_label, rhs) in enumerate(rhs_data):
            if (i%2) == 0:
                output_file.write("    RHS ")
                output_file.write(entry_template
                                  % (row_label, rhs))
            else:
                output_file.write((" "+entry_template+"\n")
                                  % (row_label, rhs))
        if (i%2) == 0:
            output_file.write("\n")


        # SOS constraints
        SOSlines = StringIO()
        sos1 = solver_capability("sos1")
        sos2 = solver_capability("sos2")
        for block in all_blocks:

            for soscondata in block.component_data_objects(
                    SOSConstraint,
                    active=True,
                    sort=sortOrder,
                    descend_into=False):

                create_symbol_func(symbol_map, soscondata, labeler)

                level = soscondata.level
                if (level == 1 and not sos1) or \
                   (level == 2 and not sos2) or \
                   (level > 2):
                    raise ValueError(
                        "Solver does not support SOS level %s constraints" % (level))
                # This updates the referenced_variable_ids, just in case
                # there is a variable that only appears in an
                # SOSConstraint, in which case this needs to be known
                # before we write the "bounds" section (Cplex does not
                # handle this correctly, Gurobi does)
                self._printSOS(symbol_map,
                               labeler,
                               variable_symbol_map,
                               soscondata,
                               SOSlines)

        #
        # BOUNDS section
        #
        output_file.write("BOUNDS\n")
        for vardata in variable_list:
            if include_all_variable_bounds or \
               (id(vardata) in self._referenced_variable_ids):
                var_label = variable_symbol_dictionary[id(vardata)]
                if vardata.fixed:
                    if not output_fixed_variable_bounds:
                        raise ValueError(
                            "Encountered a fixed variable (%s) inside an active "
                            "objective or constraint expression on model %s, which is "
                            "usually indicative of a preprocessing error. Use the "
                            "IO-option 'output_fixed_variable_bounds=True' to suppress "
                            "this error and fix the variable by overwriting its bounds "
                            "in the MPS file." % (vardata.cname(True), model.cname(True)))
                    if vardata.value is None:
                        raise ValueError("Variable cannot be fixed to a value of None.")
                    output_file.write((" FX BOUND "+entry_template+"\n")
                                      % (var_label, value(vardata.value)))
                    continue

                vardata_lb = self._get_bound(vardata.lb)
                vardata_ub = self._get_bound(vardata.ub)
                # Make it harder for -0 to show up in
                # the output. This makes file diffing
                # for test baselines slightly less
                # annoying
                if vardata_lb == 0:
                    vardata_lb = 0
                if vardata_ub == 0:
                    vardata_ub = 0
                unbounded_lb = (vardata_lb is None) or (vardata_lb == -infinity)
                unbounded_ub = (vardata_ub is None) or (vardata_ub == infinity)
                if vardata.is_binary():
                    if (vardata_lb == 0) and (vardata_ub == 1):
                        output_file.write(" BV BOUND %s\n" % (var_label))
                    else:
                        raise RuntimeError(
                            "Encountered a binary variable (%s) with bounds that "
                            "are not 0 and 1: (lb=%s, ub=%s). This can not be "
                            "represented in the MPS format. To fix this issue, "
                            "either fix the variable or changes the bounds or "
                            "the domain." % (vardata.cname(True),
                                             vardata_lb,
                                             vardata_ub))
                elif vardata.is_integer():
                    if not unbounded_lb:
                        output_file.write((" LI BOUND "+entry_template+"\n")
                                          % (var_label, vardata_lb))
                    else:
                        output_file.write(" LI BOUND %s -10E20\n" % (var_label))
                    if not unbounded_ub:
                        output_file.write((" UI BOUND "+entry_template+"\n")
                                          % (var_label, vardata_ub))
                    else:
                        output_file.write(" UI BOUND %s 10E20\n" % (var_label))
                else:
                    assert vardata.is_continuous()
                    if unbounded_lb and unbounded_ub:
                        output_file.write(" FR BOUND %s\n" % (var_label))
                    else:
                        if not unbounded_lb:
                            output_file.write((" LO BOUND "+entry_template+"\n")
                                              % (var_label, vardata_lb))
                        else:
                            output_file.write(" MI BOUND %s\n" % (var_label))

                        if not unbounded_ub:
                            output_file.write((" UP BOUND "+entry_template+"\n")
                                              % (var_label, vardata_ub))

        #
        # SOS section
        #
        output_file.write(SOSlines.getvalue())

        output_file.write("ENDATA\n")

        # Clean up the symbol map to only contain variables referenced
        # in the active constraints **Note**: warm start method may
        # rely on this for choosing the set of potential warm start
        # variables
        vars_to_delete = set(variable_symbol_map.byObject.keys()) - \
                         set(self._referenced_variable_ids.keys())
        sm_byObject = symbol_map.byObject
        sm_bySymbol = symbol_map.bySymbol
        var_sm_byObject = variable_symbol_map.byObject
        for varid in vars_to_delete:
            symbol = var_sm_byObject[varid]
            del sm_byObject[varid]
            del sm_bySymbol[symbol]
        del variable_symbol_map

        return symbol_map
