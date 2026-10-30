---
myst:
  html_meta:
    description: "Frequency response in a PowerWorld transient stability simulation from Python: trip WECC240's largest generator and plot the frequency at every bus."
---

# Frequency response

Trip the largest generator in WECC240 and measure how far the frequency falls.

```{example} frequency_response.py
:alt: Frequency at each of WECC240's buses after its largest unit trips at one second: all fall to a nadir near 59.6 Hz within a second, then recover and settle near 59.89 Hz
```
