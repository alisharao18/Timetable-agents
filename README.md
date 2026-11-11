# Multi-Agent Course Scheduling

**Live demo: https://timetable-agents.streamlit.app/**

Department and room agents learn to negotiate a university timetable using
reinforcement learning. Every timetable produced satisfies all hard
constraints: no double-booked rooms, faculty or student groups.

## College-wide version

`college-timetable/` scales this idea to a whole college: it imports the real
classroom and lab workbooks, finds and books free rooms without double
booking, and generates the full timetable with a solver plus negotiating
agents. See [college-timetable/README.md](college-timetable/README.md).

## How it works

6 department agents propose slots for their classes; 4 room agents decide
who gets contested slots. Both learn with Expected SARSA (and Q-Learning
as a comparison). Agents are only shown legal slots, so invalid timetables
cannot be produced.

The timetable grid is 5 days x 6 periods x 4 rooms (120 cells). Each session
has a soft preferred day, and earlier periods score higher. The app uses
pre-trained agents, so timetables are generated instantly with no training
at runtime.

## What the app does

- Build a scenario from scratch: add faculty (with busy days), rooms (with
  capacities) and class sessions, by hand or by importing a CSV.
- Or load a preset: Small (10 sessions), Trained scale (60 sessions) or
  Overloaded (110 sessions).
- Pick a method: Expected SARSA, Q-Learning or Greedy.
- Generate a timetable and view it by room, by department, or as a full list.
- Compare all three methods side by side.
- Download the timetable as a CSV.

### CSV format for importing sessions

```
dept,faculty,class_size,preferred_day
Computer Science,Prof 1,50,Tue
Data Science,Prof 2,60,Mon
```

A ready-made example is in [`sample_data/sessions_40.csv`](sample_data/sessions_40.csv).

## What we found

Across 5 random seeds at 75% grid load:

| method | preference | fairness | clashes |
|---|---|---|---|
| Expected SARSA | 5.62 ±0.12 | 5.14 ±0.07 | 22.6 ±4.8 |
| Q-Learning | 5.78 ±0.09 | 5.33 ±0.30 | 18.9 ±2.8 |
| Greedy | 5.95 ±0.05 | 5.68 ±0.09 | 28.2 ±5.0 |

A greedy heuristic still produces better-liked slots than either learner.
The learning agents' advantage is that they clash far less: they learn
when to concede. We also predicted that on-policy learning (Expected
SARSA) would beat off-policy (Q-Learning) under competing agents; across
5 seeds it did not.

## Limitations

- Agents trained at one scale (60 sessions); larger inputs degrade.
- Training used random faculty availability; real timetables block in
  patterns, and this is untested on real institutional data.
- Tabular Q-tables will not scale to a full university.

## Run it locally

```bash
git clone https://github.com/alisharao18/timetable-agents.git
cd timetable-agents/scheduling-app-pirate
pip install -r requirements.txt
streamlit run app.py
```

Python 3.12 is recommended.

## Project structure

```
.streamlit/config.toml        app theme
scheduling-app-pirate/
  app.py                      Streamlit interface
  scheduler.py                grid, agents, learning and validation logic
  runner.py                   loads trained agents and produces a timetable
  bridge.py                   turns user input and CSVs into a scenario
  presets.py                  ready-made demo scenarios
  theme.py                    custom styling
  trained_agents.pkl          pre-trained Q-tables
sample_data/sessions_40.csv   example sessions file for CSV import
```

## Tech stack

Python, NumPy, pandas, Streamlit. Reinforcement learning: Expected SARSA and
Q-Learning (tabular), with a greedy baseline.
