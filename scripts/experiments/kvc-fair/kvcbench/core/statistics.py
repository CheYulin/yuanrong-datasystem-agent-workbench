"""Pure logarithmic latency histogram representation; bounds are whole calls."""
import math


def bucket(ns):
    ns = max(64, ns)
    exponent = ns.bit_length() - 1
    return exponent * 64 + (ns >> (exponent - 6)) - 64



def percentile(hist, fraction):
    threshold = math.ceil(sum(hist.values()) * fraction)
    accumulated = 0
    for idx, count in sorted(hist.items()):
        accumulated += count
        if accumulated >= threshold:
            exponent, sub = divmod(idx, 64)
            return {"lower_us": ((64 + sub) << (exponent - 6)) / 1000,
                    "upper_us": ((65 + sub) << (exponent - 6)) / 1000}
    return None
