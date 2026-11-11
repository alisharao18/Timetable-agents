"""A live timetable you can place sessions on and take them off again,
with every hard rule checked on each move. The constructive solver and the
negotiating agents both work through this, so neither can make an illegal
move. (validate.py re-checks the final result without using this class.)"""
from collections import defaultdict

from constants import DAYS, LUNCH_HOURS, TT_HOURS


class Board:
    def __init__(self, p):
        self.p = p
        self.sess = {s["id"]: s for s in p.sessions}
        self.time, self.room = {}, {}                 # sid -> (day, hour) ; sid -> room id
        self.t_busy, self.g_busy, self.r_busy = {}, {}, {}
        self.load_day = defaultdict(int)              # (load id, day) -> sessions that day
        self.kids = p.children()
        self.lunch = {}                               # optional: root group -> {day: reserved hour}
        self._rooms = {}                              # cache: (kind, lab_type, size) -> rooms
        self.own = {g: p.dept_rooms.get(v["dept"], set()) for g, v in p.groups.items()}
        self.home = {}                                # division -> the classroom it settled in
        self.home_count = defaultdict(int)            # classroom -> divisions settled there

    # ---- who is where
    def leaves_of(self, g):
        return self.kids.get(g) or [g]

    def family(self, g):
        """Groups whose students would be double-booked by a class of g."""
        par = self.p.groups[g]["parent"]
        return [g] + ([par] if par else []) + list(self.kids.get(g, []))

    def leaf_busy(self, leaf, d, h):
        par = self.p.groups[leaf]["parent"]
        return (leaf, d, h) in self.g_busy or (par is not None and (par, d, h) in self.g_busy)

    def leaf_hours(self, leaf, d):
        return [h for h in TT_HOURS if self.leaf_busy(leaf, d, h)]

    def candidates(self, s):
        key = (s["kind"], (s["lab_type"] or "").upper(), s["size"])
        if key not in self._rooms:
            self._rooms[key] = sorted(self.p.rooms_for(s), key=lambda r: (r["capacity"], r["id"]))
        return self._rooms[key]

    def free_rooms(self, s, d, h):
        return [r for r in self.candidates(s)
                if all((r["id"], d, h + k) not in self.r_busy
                       and (r["id"], d, h + k) not in self.p.blocked for k in range(s["dur"]))]

    def best_room(self, s, d, h):
        own = self.own[s["group"]]
        free = self.free_rooms(s, d, h)
        if not free:
            return None
        if s["kind"] != "lecture":
            return min(free, key=lambda r: (r["id"] not in own, r["capacity"], r["id"]))["id"]
        root = self.p.root(s["group"])
        if root in self.home:
            home = self.home[root]
            return min(free, key=lambda r: (r["id"] != home, r["id"] not in own, r["capacity"], r["id"]))["id"]
        # no home yet: settle in the least-shared room, own department's first
        return min(free, key=lambda r: (r["id"] not in own, self.home_count[r["id"]],
                                        r["capacity"], r["id"]))["id"]

    # ---- the rules
    def conflicts(self, s, d, h):
        """Placed sessions that stop s from starting at (d, h): same teacher or same students."""
        out = set()
        for k in range(s["dur"]):
            x = self.t_busy.get((s["teacher"], d, h + k))
            if x:
                out.add(x)
            for g in self.family(s["group"]):
                x = self.g_busy.get((g, d, h + k))
                if x:
                    out.add(x)
        out.discard(s["id"])
        return out

    def lunch_ok(self, s, d, h, strict=False):
        hrs = {h + k for k in range(s["dur"])} & set(LUNCH_HOURS)
        if not hrs:
            return True
        if strict:
            res = self.lunch.get(self.p.root(s["group"]), {}).get(d)
            if res in hrs:
                return False
        for leaf in self.leaves_of(s["group"]):
            taken = {x for x in LUNCH_HOURS if self.leaf_busy(leaf, d, x)} | hrs
            if len(taken) == len(LUNCH_HOURS):
                return False
        t_taken = {x for x in LUNCH_HOURS if (s["teacher"], d, x) in self.t_busy} | hrs
        return len(t_taken) < len(LUNCH_HOURS)

    def day_cap(self, s):
        """How many sessions of one subject a class may have in a day: one,
        unless there are more sessions than days the class and teacher share."""
        if not hasattr(self, "_cap"):
            self._cap, count = {}, defaultdict(int)
            for x in self.p.sessions:
                count[x["load"]] += 1
            self._count = count
        if s["load"] not in self._cap:
            days = sum(any(self.p.hard_ok(s, d, h) for h in TT_HOURS) for d in DAYS)
            n = self._count[s["load"]]
            self._cap[s["load"]] = max(1, -(-n // max(1, days)))
        return self._cap[s["load"]]

    def subject_ok(self, s, d):
        return self.load_day[s["load"], d] < self.day_cap(s)

    def why_not(self, s, d, h, room=None, strict_lunch=False):
        """None if s may be placed at (d, h) [in `room`], else the reason."""
        if not self.p.hard_ok(s, d, h):
            return "outside the hours the class or teacher allows"
        if self.conflicts(s, d, h):
            return "the teacher or the students already have a class then"
        if not self.subject_ok(s, d):
            return "the class already has this subject that day"
        if not self.lunch_ok(s, d, h, strict_lunch):
            return "it would leave the students or the teacher no lunch hour"
        if room is None:
            if self.best_room(s, d, h) is None:
                return "no suitable room is free then"
        elif room not in {r["id"] for r in self.free_rooms(s, d, h)}:
            return "that room is taken or does not fit"
        return None

    # ---- moves
    def place(self, sid, d, h, room):
        s = self.sess[sid]
        self.time[sid], self.room[sid] = (d, h), room
        if s["kind"] == "lecture" and self.p.root(s["group"]) not in self.home:
            self.home[self.p.root(s["group"])] = room
            self.home_count[room] += 1
        for k in range(s["dur"]):
            self.t_busy[s["teacher"], d, h + k] = sid
            self.g_busy[s["group"], d, h + k] = sid
            self.r_busy[room, d, h + k] = sid
        self.load_day[s["load"], d] += 1

    def remove(self, sid):
        s = self.sess[sid]
        d, h = self.time.pop(sid)
        room = self.room.pop(sid)
        for k in range(s["dur"]):
            del self.t_busy[s["teacher"], d, h + k]
            del self.g_busy[s["group"], d, h + k]
            del self.r_busy[room, d, h + k]
        self.load_day[s["load"], d] -= 1
        return d, h, room

    def try_place(self, sid, d, h, room=None, strict_lunch=False):
        s = self.sess[sid]
        if self.why_not(s, d, h, room, strict_lunch):
            return False
        self.place(sid, d, h, room if room is not None else self.best_room(s, d, h))
        return True

    def load_rows(self, rows):
        """Rebuild the board from placement rows (as made by solver.to_rows)."""
        first = {}
        for r in rows:
            sid = int(str(r["session_key"])[1:])
            if sid not in first or r["hour"] < first[sid]["hour"]:
                first[sid] = r
        for sid, r in first.items():
            self.place(sid, r["day"], r["hour"], r["room_id"])
        return self
