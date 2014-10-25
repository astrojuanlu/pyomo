#  _________________________________________________________________________
#
#  Pyomo: Python Optimization Modeling Objects
#  Copyright (c) 2008 Sandia Corporation.
#  This software is distributed under the BSD License.
#  Under the terms of Contract DE-AC04-94AL85000 with Sandia Corporation,
#  the U.S. Government retains certain rights in this software.
#  For more information, see the Pyomo README.txt file.
#  _________________________________________________________________________

class ConverterError(Exception):
    """
    An exception used there is an error converting a problem.
    """

    def __init__(self,*args,**kargs):
        Exception.__init__(self,*args,**kargs)      #pragma:nocover