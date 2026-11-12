"""Shared constants: the shape of the college week."""
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
# The usual week. Sunday is used only by classes whose campus days list it
# (at MPSTME, some final and second-last years).
DEFAULT_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
TT_HOURS = list(range(8, 18))      # timetable start hours: 8 AM .. 5 PM (ends 6 PM)
BOOK_HOURS = list(range(7, 19))    # bookable start hours: 7 AM .. 6 PM, as in the sheets
LUNCH_HOURS = (12, 13, 14)         # lunch is one free hour starting 12, 1 or 2
ROLES = ("admin", "coordinator", "viewer")


def hour_label(h):
    """8 -> '8-9 AM', 12 -> '12-1 PM', 13 -> '1-2 PM'."""
    def f(x):
        return str(x if x <= 12 else x - 12)
    return f"{f(h)}-{f(h + 1)} {'AM' if h + 1 < 12 else 'PM'}"
