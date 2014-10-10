#################################################################
#                                                               #
#  October/2005                                                 #
#                                                               #
#  This problem is described in "Strategic Gaming Analysis      #
#  for Electric Power Systems: an MPEC Approach",               #
#  by Benjamin F. Hobbs, Carolyn Metzler and Jong Shi-Pang      #
#                                                               #
#  Coded by William Hart                                        #
#  Adapted from AMPL by Helena Rodrigues and Teresa Monteiro    #
#################################################################

import coopr.environ
from coopr.pyomo import *
from coopr.mpec import *


model = AbstractModel()

# SETS ------------------------------------------------------
model.N = Set()                             # set of all nodes
model.A = Set(within=model.N * model.N)     # set of all arcs
model.Sf = Set()                            # set of nodes with generators under control firm A
model.P = Set()                             # set of all generator nodes
model.D = Set()                             # set of all demand nodes
model.L = Set()                             # set of Kirchhoff's voltage loops

# PARAMETERS --------------------------------------------------
model.a = Param(model.P)                            # intercept of supply function
model.b = Param(model.P)                            # slope of supply funtion
model.c = Param(model.D)                            # intercept of demand function 
model.d = Param(model.D)                            # slope of demand function
model.alfa_s = Param(model.P)                       # upper bound of the bid 
model.alfa_i = Param(model.P)                       # lower bound of the bid 
model.QS_s = Param(model.P)                         # upper bound of production capacity 
model.Ts = Param(model.A)                           # maximum transmission capacity on arc ij
model.RR = Param(model.L, model.A, default=0)       # matrix of signed reactance coefficients
model.RRT = Param(model.A, model.L, default=0)      # transpose matrix
model.delta = Param(model.N, model.A, default=0)    # electric network
model.deltaT = Param(model.A, model.N, default=0)   # transpose matrix



# VARIABLES --------------------------------------------------
# Firs-level decision variables of firm f ....................
model.alfa = Var(model.P)   # bid for the unit at node in P     

# Primal variables in 2nd-level SPE/OPF..................
model.QS = Var(model.P)     # quantity of power generated by the unit   
model.QD = Var(model.D)     # quantity of power demanded 
model.T = Var(model.A)      # MW transmitted from i to j

# Dual variables in 2nd-level SPE/OPF ...................
model.Lambda = Var(model.N) # marginal cost at node i
model.miu = Var(model.P)    # marginal value of generation capacity for the unit at node i
model.teta = Var(model.A)   # marginal value of transmission capacity
model.gamma = Var(model.L)  # shadow price for Kirchhoff voltage law
    

# FUNCTION TO MAXIMIZE ----------------------------------------
def profit_(model):
    return sum(model.c[n]*model.QD[n]-model.d[n]*model.QD[n]**2 for n in model.D) - \
            sum(model.a[k]*model.QS[k]+(model.b[k]/2)*(model.QS[k]**2) for k in model.Sf) - \
            sum(model.teta[i,j]*model.Ts[i,j] for (i,j) in model.A) - \
            sum(0 if l in model.Sf else (model.miu[l]*model.QS_s[l]+model.a[l]*model.QS[l]+model.b[l]*model.QS[l]**2) for l in model.P)
model.profit = Objective(rule=profit_, sense=maximize)


# CONSTRAINTS ------------------------------------------------
def r1_(model, n):
    return model.alfa_i[n] <= model.alfa[n] <= model.alfa_s[n]
model.r1 = Constraint(model.P, rule=r1_)

def r2_(model, n):
    return complements(0 <= model.QS_s[n]-model.QS[n], model.miu[n] >=0)
model.r2 = Complementarity(model.P, rule=r2_)

def r3_(model, n):
    return complements(0 <= model.QS[n], model.alfa[n] - model.Lambda[n]+model.miu[n]+model.b[n]*model.QS[n] >= 0)
model.r3 = Complementarity(model.P, rule=r3_)

def r4_(model, n):
    return complements(0 <= model.QD[n], model.Lambda[n] - model.c[n] + model.d[n]*model.QD[n] >= 0)
model.r4 = Complementarity(model.D, rule=r4_)

def r5_(model, i, j):
    return complements(0 <= model.teta[i,j], model.Ts[i,j] - model.T[i,j] >= 0)
model.r5 = Complementarity(model.A, rule=r5_)

def r6_(model, i, j):
    return complements(0 <= model.T[i,j], \
            sum(model.deltaT[i,j,n]*model.Lambda[n] for n in model.N) + teta[i,j] + sum(model.RRT[i,j,k]*model.gamma[k] for k in model.L) >= 0)
model.r6 = Complementarity(model.A, rule=r6_)

def r7_(model, n):
    return (model.QD[n] if n in model.D else 0) - \
           (model.QS[n] if n in model.P else 0) + \
           sum(model.delta[n,i,j]*model.T[i,j] for (i,j) in model.A) == 0
model.r7 = Constraint(model.N, rule=r7_)

def r8_(model, k):
    return sum(model.RR[k,i,j]*model.T[i,j] for (i,j) in model.A) == 0
model.r8 = Constraint(model.L, rule=r8_)

