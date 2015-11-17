#  _________________________________________________________________________
#
#  Pyomo: Python Optimization Modeling Objects
#  Copyright (c) 2014 Sandia Corporation.
#  Under the terms of Contract DE-AC04-94AL85000 with Sandia Corporation,
#  the U.S. Government retains certain rights in this software.
#  This software is distributed under the BSD License.
#  _________________________________________________________________________
#
# Farmer: Annotated with location of stochastic matrix entries
#         for use with pysp2smps conversion tool.
#
# Imports
#

from pyomo.core import *
# Note: Components from 'beta' subdirectories
#       have no guarantees about documentation or
#       existence in future releases.
from pyomo.core.beta.dict_objects import ConstraintDict
from pyomo.core.base.constraint import _GeneralConstraintData as ConstraintObject

#
# Model
#

model = AbstractModel()

#
# Sets
#

model.CROPS = Set(initialize=['WHEAT', 'CORN', 'SUGAR_BEETS'],
                  ordered=True)

#
# Parameters
#

model.TOTAL_ACREAGE = Param(within=PositiveReals)

model.PriceQuota = Param(model.CROPS, within=PositiveReals)

model.SubQuotaSellingPrice = Param(model.CROPS, within=PositiveReals)

def super_quota_selling_price_validate (model, value, i):
    return model.SubQuotaSellingPrice[i] >= model.SuperQuotaSellingPrice[i]

model.SuperQuotaSellingPrice = Param(model.CROPS,
                                     validate=super_quota_selling_price_validate)

model.CattleFeedRequirement = Param(model.CROPS, within=NonNegativeReals)

model.PurchasePrice = Param(model.CROPS, within=PositiveReals)

model.PlantingCostPerAcre = Param(model.CROPS, within=PositiveReals)

model.Yield = Param(model.CROPS, within=NonNegativeReals)

#
# Variables
#

model.DevotedAcreage = Var(model.CROPS,
                           bounds=(0.0, model.TOTAL_ACREAGE))
model.QuantitySubQuotaSold = Var(model.CROPS,
                                 bounds=(0.0, None))
model.QuantitySuperQuotaSold = Var(model.CROPS,
                                   bounds=(0.0, None))
model.QuantityPurchased = Var(model.CROPS,
                              bounds=(0.0, None))

#
# Constraints
#

def ConstrainTotalAcreage_rule(model):
    return summation(model.DevotedAcreage) <= model.TOTAL_ACREAGE
model.ConstrainTotalAcreage = \
    Constraint(rule=ConstrainTotalAcreage_rule)

def EnforceQuotas_rule(model, i):
    return (0.0, model.QuantitySubQuotaSold[i], model.PriceQuota[i])
model.EnforceQuotas = Constraint(model.CROPS,
                                 rule=EnforceQuotas_rule)

#
# Constraints With Stochastic Entries
#
# SMPS Related Suffix Data
model.PySP_StochasticMatrix = Suffix()
# Note: The following constraints are built in concrete fashion inside
#       a single BuildAction rule using the ConstraintDict
#       prototype. This component is experimental and its use is not
#       necessary to populate the PySP_StochasticMatrix Suffix object.
#       We use it here for convenience so that the Suffix can be
#       populated next to the constraint expression rather than using
#       a separate Suffix initialization rule or BuildAction.
model.EnforceCattleFeedRequirement = ConstraintDict()
model.LimitAmountSold = ConstraintDict()
def stochastic_constraints_rule(model):
    for i in model.CROPS:

        model.EnforceCattleFeedRequirement[i] = \
            ConstraintObject(model.CattleFeedRequirement[i] <=
                             (model.Yield[i] * model.DevotedAcreage[i]) + \
                             model.QuantityPurchased[i] - \
                             model.QuantitySubQuotaSold[i] - \
                             model.QuantitySuperQuotaSold[i])
        # tag which variable in the above constraint has a stochastic
        # coefficient
        model.PySP_StochasticMatrix[
            model.EnforceCattleFeedRequirement[i]] = \
                (model.DevotedAcreage[i],)

        model.LimitAmountSold[i] = \
            ConstraintObject(model.QuantitySubQuotaSold[i] + \
                             model.QuantitySuperQuotaSold[i] - \
                             (model.Yield[i] * model.DevotedAcreage[i]) <= 0.0)
        # tag which variable in the above constraint has a stochastic
        # coefficient
        model.PySP_StochasticMatrix[
            model.LimitAmountSold[i]] = \
                (model.DevotedAcreage[i],)
model.build_stochastic_constraints = BuildAction(rule=stochastic_constraints_rule)

#
# Stage-specific cost computations
#

def ComputeFirstStageCost_rule(model):
    return summation(model.PlantingCostPerAcre, model.DevotedAcreage)
model.FirstStageCost = Expression(rule=ComputeFirstStageCost_rule)

def ComputeSecondStageCost_rule(model):
    expr = summation(model.PurchasePrice, model.QuantityPurchased)
    expr -= summation(model.SubQuotaSellingPrice, model.QuantitySubQuotaSold)
    expr -= summation(model.SuperQuotaSellingPrice, model.QuantitySuperQuotaSold)
    return expr
model.SecondStageCost = Expression(rule=ComputeSecondStageCost_rule)

#
# PySP Auto-generated Objective
#
# minimize: sum of StageCosts
#
# An active scenario objective equivalent to that generated by PySP is
# included here for informational purposes.
def total_cost_rule(model):
    return model.FirstStageCost + model.SecondStageCost
model.Total_Cost_Objective = Objective(rule=total_cost_rule,
                                       sense=minimize)