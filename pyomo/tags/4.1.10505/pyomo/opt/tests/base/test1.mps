NAME	TINYMATCHING
ROWS
 N   OBJ
 E   NODEA1
 E   NODEA2
 E   NODEA3
 E   NODEB1
 E   NODEB2
 E   NODEB3
COLUMNS
    X11	OBJ	1	NODEA1	1
    X11	NODEB1	1
    X12	OBJ	2	NODEA1	1
    X12	NODEB2	1
    X13	OBJ	3	NODEA1	1
    X13	NODEB3	1
    X21	OBJ	2	NODEA2	1
    X21	NODEB1	1
    X22	OBJ	3	NODEA2	1
    X22	NODEB2	1
    X23	OBJ	1	NODEA2	1
    X23	NODEB3	1
    X31	OBJ	3	NODEA3	1
    X31	NODEB1	1
    X32	OBJ	1	NODEA3	1
    X32	NODEB2	1
    X33	OBJ	2	NODEA3	1
    X33	NODEB3	1
RHS
 rhs	NODEA1	1	NODEA2	1
 rhs	NODEA3	1	NODEB1	1
 rhs	NODEB2	1	NODEB3	1
BOUNDS
 UP BOUND X11 1
 UP BOUND X12 1
 UP BOUND X13 1
 UP BOUND X21 1
 UP BOUND X22 1
 UP BOUND X23 1
 UP BOUND X31 1
 UP BOUND X32 1
 UP BOUND X33 1
ENDATA