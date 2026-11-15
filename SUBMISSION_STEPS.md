# Submission steps (Project 4)

Do this on your computer, in the terminal. Replace `YOUR-USER` with your GitHub username.

## 1. Create the new repo (once)
1. github.com → **New repository** → name `applied-ai-system-final` → **Public** → do NOT add README/.gitignore → Create.

## 2. Put the files in place
```bash
cd ~/path/where/you/keep/projects
git clone https://github.com/YOUR-USER/<your-module-2-repo>.git applied-ai-system-final
cd applied-ai-system-final
git remote set-url origin https://github.com/YOUR-USER/applied-ai-system-final.git
git rm -r --cached __pycache__ data.json 2>/dev/null   # these should not be tracked
```
Now unzip `applied-ai-system-final.zip` and copy everything inside it over this folder (overwrite when asked).
Delete the old Module 2 files that moved: `README.md`, `reflection.md` and `ai_interactions.md` are replaced/archived
(the zip's versions win; the old ones are in `docs/module2/`), and the old `diagrams/uml.mmd` if present.

## 3. Check it works
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest              # expect: 108 passed
python run_eval.py            # expect: 21/21 cases passed
python coach.py --demo
streamlit run app.py          # try the Care Coach box, click Draft, then Approve
```
Paste your own `python -m pytest -q` result into the Testing section of `README.md`.

## 4. Fill in the two TODOs (graders read these)
- `model_card.md` → section 6: 2-3 sentences in your own words.
- `ai_interactions.md` → section 1: what you checked yourself.
Optional: add a screenshot of the app to `assets/` and link it in the README.

## 5. Commit in several meaningful steps, then push
```bash
git add knowledge/ care_coach/__init__.py care_coach/knowledge.py care_coach/retriever.py
git commit -m "Add knowledge base and BM25 retriever for Care Coach"
git add care_coach/parser.py care_coach/guardrails.py
git commit -m "Add request parser and input guardrails"
git add care_coach/planner.py care_coach/explain.py coach.py
git commit -m "Add agent loop (draft, critic, revise, approve) and CLI"
git add eval/ care_coach/evaluation.py run_eval.py tests/ scripts/
git commit -m "Add evaluation harness, baseline comparison and tests"
git add app.py
git commit -m "Add Care Coach section to the Streamlit app"
git add -A
git commit -m "Add architecture diagram, README, model card and AI log"
git push -u origin main
```

## 6. Submit
Open `https://github.com/YOUR-USER/applied-ai-system-final` in a private/incognito window to confirm it is public and
shows `README.md`, `model_card.md`, `diagrams/architecture.mmd`, `assets/`. Submit that URL.
