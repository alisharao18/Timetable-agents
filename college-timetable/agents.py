"""Negotiation: department agents and room agents improve a valid timetable.

The solver hands over a timetable that already obeys every hard rule. Here
each DEPARTMENT agent speaks for its own classes and teachers and learns
which kind of request is worth making; each ROOM agent (one per floor)
decides who gets a room when two departments want the same one, and learns
which way of deciding leaves the college better off.

Both learn with Expected SARSA (Q-learning is available for comparison),
the same methods as the original small-scale project. Every request is
applied through Board, so an agent cannot make an illegal move, and a move
is kept only if it makes the timetable no worse.
"""
import random
import time
from collections import defaultdict

import numpy as np

import solver
import validate
from board import Board
from constants import DAYS, TT_HOURS

ACTIONS = ["move_worst", "swap_inside", "claim_own_room", "ask_room_swap", "hold"]
RULES = ["biggest_gain", "least_satisfied", "best_fit", "own_department"]
UNPLACED = 10000


# ------------------------------------------------------------ bookkeeping
class Ledger:
    """Penalty accounting on a Board, using the same weights as validate.score."""

    def __init__(self, p, b):
        self.p, self.b = p, b
        self.by_root, self.by_dept = defaultdict(list), defaultdict(list)
        for s in p.sessions:
            self.by_root[p.root(s["group"])].append(s)
            self.by_dept[p.groups[s["group"]]["dept"]].append(s)
        self.kinds_owned = {d: {r["kind"] for r in p.rooms if r["id"] in ids}
                            for d, ids in p.dept_rooms.items()}

    def lonely(self, s):
        """True if none of the class's other lessons touch this one."""
        d, h = self.b.time[s["id"]]
        for leaf in self.b.leaves_of(s["group"]):
            if self.b.leaf_busy(leaf, d, h - 1) or self.b.leaf_busy(leaf, d, h + s["dur"]):
                return False
        return True

    def in_own_room(self, s):
        dept = self.p.groups[s["group"]]["dept"]
        want = "lab" if s["kind"] == "lab" else "classroom"
        if want not in self.kinds_owned.get(dept, ()):
            return True                                   # nothing to claim
        return self.b.room[s["id"]] in self.p.dept_rooms[dept]

    def unhappiness(self, s):
        if s["id"] not in self.b.time:
            return UNPLACED
        d, h = self.b.time[s["id"]]
        return 10 * self.p.soft_cost(s, d, h) + (3 if self.lonely(s) else 0) + \
            (0 if self.in_own_room(s) else 2)

    def family(self, root):
        """(core penalty, hours outside own rooms) for one division and its batches."""
        core = away = 0
        for s in self.by_root[root]:
            if s["id"] not in self.b.time:
                core += UNPLACED * s["dur"]
                continue
            d, h = self.b.time[s["id"]]
            core += 10 * self.p.soft_cost(s, d, h)
            away += 0 if self.in_own_room(s) else s["dur"]
        md = self.p.pref_of(root).get("max_days")
        for leaf in self.b.leaves_of(root):
            used = 0
            for d in DAYS:
                hrs = self.b.leaf_hours(leaf, d)
                if hrs:
                    used += 1
                    core += 3 * max(0, (hrs[-1] - hrs[0] + 1) - len(hrs) - 1)
            if md and used > md:
                core += 80 * (used - md)
        return core, away

    def teacher(self, t):
        md = self.p.tpref.get(t, {}).get("max_days")
        if not md:
            return 0
        days = sum(any((t, d, h) in self.b.t_busy for h in TT_HOURS) for d in DAYS)
        return 50 * max(0, days - md)

    def measure(self, sids):
        roots = {self.p.root(self.b.sess[x]["group"]) for x in sids}
        core = away = 0
        for r in roots:
            c, a = self.family(r)
            core, away = core + c, away + a
        core += sum(self.teacher(t) for t in {self.b.sess[x]["teacher"] for x in sids})
        return core, away

    def dept_penalty(self, dept):
        roots = {self.p.root(s["group"]) for s in self.by_dept[dept]}
        hours = sum(s["dur"] for s in self.by_dept[dept]) or 1
        return sum(self.family(r)[0] for r in roots) / hours


def try_move(b, led, relocs, keep_if_worse=False):
    """Apply [(sid, day, hour, room or None), ...] as ONE move.
    -> (ok, gain). The move is undone unless it is legal and makes things
    no worse (strictly better, or equal with more classes in their own rooms)."""
    sids = [r[0] for r in relocs]
    if any(x in b.fixed for x in sids):                # locked and elective classes never move
        return False, 0.0
    before = led.measure(sids)
    saved = {x: (b.time[x], b.room[x]) for x in sids if x in b.time}
    for x in saved:
        b.remove(x)

    def undo():
        for x in sids:
            if x in b.time:
                b.remove(x)
        for x, ((d, h), room) in saved.items():
            b.place(x, d, h, room)

    for sid, d, h, room in relocs:
        if not b.try_place(sid, d, h, room, strict_lunch=True):
            undo()
            return False, 0.0
    after = led.measure(sids)
    gain = (before[0] - after[0]) + 0.01 * (before[1] - after[1])
    better = after[0] < before[0] or (after[0] == before[0] and after[1] < before[1])
    if not better and not keep_if_worse:
        undo()
        return False, gain
    return True, gain


# ------------------------------------------------------------ learners
class Learner:
    def __init__(self, n_actions, algo, rng, alpha=0.2, gamma=0.9):
        self.Q = defaultdict(lambda: np.zeros(n_actions))
        self.n, self.algo, self.rng = n_actions, algo, rng
        self.alpha, self.gamma, self.eps = alpha, gamma, 0.3
        self.used = np.zeros(n_actions, dtype=int)
        self.total_reward = 0.0

    def probs(self, state):
        q = self.Q[state]
        pr = np.full(self.n, self.eps / self.n)
        best = np.flatnonzero(q == q.max())
        pr[best] += (1 - self.eps) / len(best)
        return pr

    def choose(self, state):
        a = int(self.rng.choice(self.n, p=self.probs(state)))
        self.used[a] += 1
        return a

    def learn(self, state, action, reward, nxt):
        self.total_reward += reward
        if self.algo == "q_learning":
            target = reward + self.gamma * self.Q[nxt].max()
        else:                                             # expected SARSA
            target = reward + self.gamma * float(self.probs(nxt) @ self.Q[nxt])
        self.Q[state][action] += self.alpha * (target - self.Q[state][action])


class DeptAgent:
    """Speaks for one department's classes."""

    def __init__(self, dept, name, led, algo, rng, pyrng):
        self.dept, self.name, self.led, self.rng = dept, name, led, pyrng
        self.brain = Learner(len(ACTIONS), algo, rng)
        self.last = 0                                     # 0 nothing, 1 won, 2 lost
        self.sessions = led.by_dept[dept]

    def state(self, frac):
        pen = self.led.dept_penalty(self.dept)
        mood = 0 if pen < 0.5 else 1 if pen < 1.5 else 2 if pen < 4 else 3
        return (mood, min(3, int(frac * 4)), self.last)

    # each action returns a list of proposals: dict(relocs, gain, cell, sid)
    def propose(self, action, bids=3):
        b, led = self.led.b, self.led
        out = []
        if action == "hold" or not self.sessions:
            return out
        if action == "move_worst":
            pool = sorted(self.sessions, key=led.unhappiness, reverse=True)[:12]
            pool = [s for s in pool if led.unhappiness(s) > 0]
            for s in self.rng.sample(pool, min(bids, len(pool))):
                best = self._best_slot(s)
                if best:
                    out.append(best)
        elif action == "swap_inside":
            # trade times between an unhappy class hour and another lesson of the same division
            pool = [s for s in sorted(self.sessions, key=led.unhappiness, reverse=True)[:12]
                    if s["id"] in b.time and led.unhappiness(s) > 0]
            for s1 in self.rng.sample(pool, min(bids, len(pool))):
                best = None
                mates = [x for x in led.by_root[led.p.root(s1["group"])]
                         if x["id"] != s1["id"] and x["dur"] == s1["dur"] and x["id"] in b.time
                         and b.time[x["id"]] != b.time[s1["id"]]]
                for s2 in self.rng.sample(mates, min(10, len(mates))):
                    (d1, h1), (d2, h2) = b.time[s1["id"]], b.time[s2["id"]]
                    cand = self._probe([(s1["id"], d2, h2, None), (s2["id"], d1, h1, None)], s1)
                    if cand and (best is None or cand["gain"] > best["gain"]):
                        best = cand
                if best:
                    out.append(best)
        else:                                             # the two room actions
            away = [s for s in self.sessions if s["id"] in b.time and not led.in_own_room(s)]
            own = led.p.dept_rooms.get(self.dept, set())
            for s in self.rng.sample(away, min(bids * 2, len(away))):
                d, h = b.time[s["id"]]
                fits = [r["id"] for r in b.candidates(s) if r["id"] in own]
                free = {r["id"] for r in b.free_rooms(s, d, h)}
                if action == "claim_own_room":
                    mine = [r for r in fits if r in free]
                    if mine:
                        p = self._probe([(s["id"], d, h, mine[0])], s)
                        if p:
                            out.append(p)
                else:                                     # ask whoever sits there to trade rooms
                    for r in fits:
                        o = b.r_busy.get((r, d, h))
                        if not o or b.time[o] != (d, h) or b.sess[o]["dur"] != s["dur"]:
                            continue
                        mine_now = b.room[s["id"]]
                        if mine_now not in {x["id"] for x in b.candidates(b.sess[o])}:
                            continue
                        p = self._probe([(s["id"], d, h, r), (o, d, h, mine_now)], s)
                        if p:                             # the sitting class gets a say too
                            p["against"] = dict(dept=led.p.groups[b.sess[o]["group"]]["dept"],
                                                gain=0.0, cell=p["cell"], size=b.sess[o]["size"],
                                                dur=s["dur"], relocs=[], stay=True)
                            out.append(p)
                            break
                if len(out) >= bids:
                    break
        return out

    def _probe(self, relocs, s):
        """Try a move, note its gain, and put everything back."""
        b, led = self.led.b, self.led
        snap = {x: (b.time[x], b.room[x]) for x, *_ in relocs if x in b.time}
        ok, gain = try_move(b, led, relocs)
        if not ok:
            return None
        sid = relocs[0][0]
        cell = (b.room[sid], b.time[sid][0], b.time[sid][1])
        final = [(x, *b.time[x], b.room[x]) for x, *_ in relocs]
        for x, *_ in relocs:
            b.remove(x)
        for x, ((d, h), room) in snap.items():
            b.place(x, d, h, room)
        return dict(dept=self.dept, relocs=final, gain=gain, cell=cell, sid=sid,
                    size=s["size"], dur=s["dur"])

    def _best_slot(self, s):
        b, p = self.led.b, self.led.p
        here = b.time.get(s["id"])
        scored = []
        for d in DAYS:
            for h in TT_HOURS:
                if (d, h) == here or not p.hard_ok(s, d, h):
                    continue
                near = any(b.leaf_busy(leaf, d, h - 1) or b.leaf_busy(leaf, d, h + s["dur"])
                           for leaf in b.leaves_of(s["group"]))
                scored.append((10 * p.soft_cost(s, d, h) - (3 if near else 0) + self.rng.random(), d, h))
        best = None
        for _, d, h in sorted(scored)[:12]:
            cand = self._probe([(s["id"], d, h, None)], s)
            if cand and (best is None or cand["gain"] > best["gain"]):
                best = cand
        return best


class RoomAgent:
    """Arbitrates on its floor: when two departments ask for the same room,
    or when one asks for a room another department's class is sitting in."""

    def __init__(self, floor, algo, rng):
        self.floor = floor
        self.brain = Learner(len(RULES), algo, rng)

    def pick(self, bids, frac, led, caps):
        state = (min(len(bids), 4), min(3, int(frac * 4)))
        a = self.brain.choose(state)
        rule = RULES[a]
        if rule == "biggest_gain":
            key = lambda x: -x["gain"]
        elif rule == "least_satisfied":
            key = lambda x: -led.dept_penalty(x["dept"])
        elif rule == "best_fit":
            key = lambda x: caps[x["cell"][0]] - x["size"]
        else:
            key = lambda x: x["cell"][0] not in led.p.dept_rooms.get(x["dept"], ())
        return min(bids, key=lambda x: (key(x), -x["gain"])), state, a


# ------------------------------------------------------------ the negotiation
def negotiate(p, rows, rounds=200, algo="expected_sarsa", seed=0, bids=3,
              max_seconds=None, progress=None):
    """-> (improved rows, report). Never returns a worse or an invalid timetable."""
    rng, pyrng = np.random.default_rng(seed), random.Random(seed)
    b = Board(p).load_rows(rows)
    solver.reserve_lunch(p, b)
    led = Ledger(p, b)
    before = validate.score(p, rows)
    depts = [DeptAgent(d, p.depts.get(d, "No department"), led, algo, rng, pyrng)
             for d in sorted(led.by_dept, key=lambda x: (x is None, x))]
    floor_of = {r["id"]: r["floor"] for r in p.rooms}
    caps = {r["id"]: r["capacity"] for r in p.rooms}
    room_agents = {f: RoomAgent(f, algo, rng) for f in sorted({v for v in floor_of.values()}, key=str)}
    dept_before = {a.name: round(led.dept_penalty(a.dept), 3) for a in depts}
    stats = dict(accepted=0, rejected=0, contested=0, conceded=0, proposals=0)
    history, t0 = [], time.time()

    for rnd in range(rounds):
        frac = rnd / max(1, rounds)
        for a in depts:                                   # explore early, exploit late
            a.brain.eps = 0.3 * (1 - frac) + 0.03
        for ra in room_agents.values():
            ra.brain.eps = 0.3 * (1 - frac) + 0.03
        turn, all_bids = {}, []
        for a in depts:
            st = a.state(frac)
            act = a.brain.choose(st)
            props = a.propose(ACTIONS[act], bids)
            # asking for nothing useful costs a little; so does sitting still while unhappy
            idle = -0.05 * st[0] if ACTIONS[act] == "hold" else (0.0 if props else -0.2)
            turn[a.dept] = [st, act, idle, 0]
            all_bids += props
        stats["proposals"] += len(all_bids)

        # bids that want the same room in overlapping hours are a contest
        wants = defaultdict(list)
        for i, x in enumerate(all_bids):
            room, d, h = x["cell"]
            for k in range(x["dur"]):
                wants[room, d, h + k].append(i)
        contest_of = {}
        for cell, idx in wants.items():
            if len({all_bids[i]["dept"] for i in idx}) > 1:
                group = set(idx)
                for i in idx:
                    group |= contest_of.get(i, set())
                for i in group:
                    contest_of[i] = group
        winners, done, judged = [], set(), []
        for i, x in enumerate(all_bids):
            if i in done:
                continue
            if i not in contest_of and "against" not in x:
                winners.append(x)
                continue
            group = [all_bids[j] for j in sorted(contest_of.get(i, {i}))]
            group += [g["against"] for g in group if "against" in g]   # whoever sits there now
            done |= contest_of.get(i, {i})
            ra = room_agents[floor_of[x["cell"][0]]]
            win, st, act = ra.pick(group, frac, led, caps)
            stats["contested"] += 1
            stats["conceded"] += sum(1 for g in group if g is not win and not g.get("stay"))
            if not win.get("stay"):
                winners.append(win)
            judged.append((ra, st, act, win, len(group)))
            for lose in group:
                if lose is not win and not lose.get("stay"):
                    turn[lose["dept"]][2] -= 0.5
                    turn[lose["dept"]][3] = 2

        pyrng.shuffle(winners)
        gained = {}
        for x in winners:
            ok, gain = try_move(b, led, [(sid, d, h, room) for sid, d, h, room in x["relocs"]])
            gained[id(x)] = gain if ok else None
            if ok:
                stats["accepted"] += 1
                turn[x["dept"]][2] += gain
                turn[x["dept"]][3] = turn[x["dept"]][3] or 1
            else:                                         # an earlier winner changed the picture
                stats["rejected"] += 1
                turn[x["dept"]][2] -= 0.2
        nxt = (rnd + 1) / max(1, rounds)
        for a in depts:
            st, act, reward, outcome = turn[a.dept]
            a.last = outcome
            a.brain.learn(st, act, reward, a.state(nxt))
        for ra, st, act, win, n in judged:
            g = 0.0 if win.get("stay") else gained.get(id(win))
            ra.brain.learn(st, act, -1.0 if g is None else g, (min(n, 4), min(3, int(nxt * 4))))
        if rnd % 5 == 0 or rnd == rounds - 1:
            total = sum(led.family(r)[0] for r in led.by_root) + sum(led.teacher(t) for t in p.teachers)
            history.append((rnd + 1, round(total, 1)))
            if progress:
                progress(rnd + 1, rounds, history[-1][1])
        if max_seconds and time.time() - t0 > max_seconds:
            break

    out = solver.board_rows(p, b)
    report = dict(
        algo=algo, rounds=rnd + 1 if rounds else 0, seconds=round(time.time() - t0, 1),
        before=before, after=validate.score(p, out), history=history, **stats,
        per_dept={a.name: dict(before=dept_before[a.name], after=round(led.dept_penalty(a.dept), 3),
                               reward=round(a.brain.total_reward, 1),
                               actions=dict(zip(ACTIONS, map(int, a.brain.used)))) for a in depts},
        room_rules={f"Floor {f}": dict(zip(RULES, map(int, ra.brain.used)))
                    for f, ra in room_agents.items() if ra.brain.used.sum()})
    return out, report
