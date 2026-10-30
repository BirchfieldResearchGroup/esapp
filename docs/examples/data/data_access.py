"""Data access

Read fields into DataFrames, select objects with conditions, and write values back.
"""

from esapp import PowerWorld
from esapp.grid import Bus, Gen, Load

with PowerWorld("Hawaii40.pwb") as pw:
    buses = pw[Bus, [Bus.BusName, Bus.BusNomVolt]]  # keys first, then the fields you asked for
    print(buses.head(3))

    high = pw[Bus, Bus.BusName, (Bus.BusNomVolt > 100) & Bus.BusName.startswith("W")]
    print(high)

    print(pw[Gen(2, "1"), [Gen.GenMW, Gen.GenStatus]])  # one object

    with pw.snapshot():  # undone when the block ends
        pw[Load.LoadSMW] = pw[Load.LoadSMW] * 1.1  # a Series, matched to each load by key
        pw[Gen, Gen.GenMW, Gen.GenMW > 100] = 100.0  # sets only the matching generators
        print(f"{pw[Load.LoadSMW].sum():.1f} MW of load")
    print(f"{pw[Load.LoadSMW].sum():.1f} MW once restored")
