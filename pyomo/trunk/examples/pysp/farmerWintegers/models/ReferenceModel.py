# Farmer: rent out version has a scalar root node var
# note: this will minimize
#
# Imports
#

from coopr.pyomo import *

#
# Model
#

model = AbstractModel()

#
# Parameters
#

model.CROPS = Set()

model.TOTAL_ACREAGE = Param(within=PositiveReals)

model.PriceQuota = Param(model.CROPS, within=PositiveReals)

model.SubQuotaSellingPrice = Param(model.CROPS, within=PositiveReals)

def super_quota_selling_price_validate (model, value, i):
    return model.SubQuotaSellingPrice[i] >= model.SuperQuotaSellingPrice[i]

model.SuperQuotaSellingPrice = Param(model.CROPS, validate=super_quota_selling_price_validate)

model.CattleFeedRequirement = Param(model.CROPS, within=NonNegativeReals)

model.PurchasePrice = Param(model.CROPS, within=PositiveReals)

model.PlantingCostPerAcre = Param(model.CROPS, within=PositiveReals)

model.Yield = Param(model.CROPS, within=NonNegativeReals)

#
# Variables
#

model.DevotedAcreage = Var(model.CROPS, bounds=(0.0, model.TOTAL_ACREAGE), within=NonNegativeIntegers)

model.QuantitySubQuotaSold = Var(model.CROPS, bounds=(0.0, None))
model.QuantitySuperQuotaSold = Var(model.CROPS, bounds=(0.0, None))

model.QuantityPurchased = Var(model.CROPS, bounds=(0.0, None))

model.FirstStageCost = Var()
model.SecondStageCost = Var()

#
# Constraints
#

def ConstrainTotalAcreage_rule(model):
    return summation(model.DevotedAcreage) <= model.TOTAL_ACREAGE

model.ConstrainTotalAcreage = Constraint()

def EnforceCattleFeedRequirement_rule(model, i):
    return model.CattleFeedRequirement[i] <= (model.Yield[i] * model.DevotedAcreage[i]) + model.QuantityPurchased[i] - model.QuantitySubQuotaSold[i] - model.QuantitySuperQuotaSold[i]

model.EnforceCattleFeedRequirement = Constraint(model.CROPS)

def LimitAmountSold_rule(model, i):
    return model.QuantitySubQuotaSold[i] + model.QuantitySuperQuotaSold[i] - (model.Yield[i] * model.DevotedAcreage[i]) <= 0.0

model.LimitAmountSold = Constraint(model.CROPS)

def EnforceQuotas_rule(model, i):
    return (0.0, model.QuantitySubQuotaSold[i], model.PriceQuota[i])

model.EnforceQuotas = Constraint(model.CROPS)

#
# Stage-specific cost computations
#

def ComputeFirstStageCost_rule(model):
    return model.FirstStageCost - summation(model.PlantingCostPerAcre, model.DevotedAcreage) == 0.0

model.ComputeFirstStageCost = Constraint()

def ComputeSecondStageCost_rule(model):
    expr = summation(model.PurchasePrice, model.QuantityPurchased)
    expr -= summation(model.SubQuotaSellingPrice, model.QuantitySubQuotaSold)
    expr -= summation(model.SuperQuotaSellingPrice, model.QuantitySuperQuotaSold)
    return (model.SecondStageCost - expr) == 0.0

model.ComputeSecondStageCost = Constraint()

#
# PySP Auto-generated Objective
#
# minimize: sum of StageCostVariables
#
# A active scenario objective equivalent to that generated by PySP is
# included here for informational purposes.
def total_cost_rule(model):
    return model.FirstStageCost + model.SecondStageCost
model.Total_Cost_Objective = Objective(rule=total_cost_rule, sense=minimize)
