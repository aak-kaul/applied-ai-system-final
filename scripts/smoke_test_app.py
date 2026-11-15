"""Run app.py top to bottom against a mock streamlit to catch NameErrors / wrong API use."""
import sys, types, os, runpy
from datetime import date, time
from unittest.mock import MagicMock

class Session(dict):
    __getattr__ = dict.get
    def __setattr__(self, k, v): self[k] = v

class Ctx:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __getattr__(self, n): return MagicMock()

def make_st(click):
    st = MagicMock()
    st.session_state = Session()
    st.cache_resource = lambda f: f
    st.sidebar = Ctx()
    st.form = lambda *a, **k: Ctx()
    st.expander = lambda *a, **k: Ctx()
    st.spinner = lambda *a, **k: Ctx()
    def columns(spec, **k):
        n = spec if isinstance(spec, int) else len(spec)
        def col():
            c = MagicMock(**{"text_input.return_value": "x", "number_input.return_value": 1,
                             "date_input.return_value": date(2026,1,5), "time_input.return_value": time(8,0),
                             "checkbox.return_value": True})
            c.selectbox = lambda label, options, **k: options[0]
            c.button = lambda label, **k: label in click
            return c
        return [col() for _ in range(n)]
    st.columns = columns
    st.tabs = lambda labels: [Ctx() for _ in labels]
    st.text_input = lambda label, value="", **k: value
    st.text_area = lambda label, value="", **k: value
    st.number_input = lambda *a, value=1, **k: value
    st.selectbox = lambda label, options, **k: options[0]
    st.date_input = lambda *a, **k: date(2026, 1, 5)
    st.time_input = lambda *a, value=time(8,0), **k: value
    st.checkbox = lambda *a, value=True, **k: value
    st.form_submit_button = lambda *a, **k: False
    st.button = lambda label, **k: label in click
    st.rerun = MagicMock(side_effect=lambda: None)
    return st

def run(click, session=None):
    st = make_st(click)
    if session: st.session_state.update(session)
    sys.modules["streamlit"] = st
    os.chdir(os.environ["APPDIR"]); sys.path.insert(0, os.environ["APPDIR"])
    runpy.run_path("app.py", run_name="__main__")
    return st

if __name__ == "__main__":
    st = run({"Draft a plan"})
    plan = st.session_state["coach_plan"]
    print("draft ->", plan.status, len(plan.scheduled_items), "scheduled,", len(plan.unscheduled_items), "unplaced")
    st2 = run({"✅ Approve and add to my schedule"}, {"coach_plan": plan})
    owner = st2.session_state["owner"]
    print("approve -> owner now has pets", [p.name for p in owner.pets], "and", len(owner.get_all_tasks()), "tasks;", st2.session_state.get("coach_flash"))
    st3 = run({"Draft a plan"}, {"coach_plan": None, "owner": owner})
    print("second draft ok:", st3.session_state["coach_plan"].status)
