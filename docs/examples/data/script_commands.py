"""Script commands

Call any PowerWorld script command as a typed Python class, and send several in one call.
"""

from esapp import PowerWorld
from esapp.commands import CTGAutoInsert, CTGSolveAll, SolvePowerFlow
from esapp.grid import Contingency, Gen

print(SolvePowerFlow("POLARNEWTON"))  # printing a command shows its script text
print(CTGSolveAll(do_distributed=False))

with PowerWorld("Hawaii40.pwb") as pw:
    pw.run(
        SolvePowerFlow("POLARNEWTON"), CTGAutoInsert(), CTGSolveAll()
    )  # sent to Simulator in one call
    print(len(pw[Contingency]), "contingencies solved")

    pw.run("SetData", Gen, [Gen.GenMW], [50.0], Gen.BusNum.eq(2))  # any command, by name
    print(pw[Gen, Gen.GenMW, Gen.BusNum.eq(2)])
