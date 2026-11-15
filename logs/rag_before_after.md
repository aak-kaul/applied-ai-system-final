# RAG before / after adding a custom document

Input: `Biscuit is a 3 year old dog. I'm free 07:00-21:00.`

Before: only the shared knowledge files (`python coach.py "..." --no-custom`).

```text
PLAN for Biscuit (dog, adult) on Monday 2026-01-05   confidence 0.98  status: OK
  TIME         TASK                                         WHO         SOURCE
  07:00-07:15  Adult dog meal                               You         dog_care.md > Adult dog feeding
  07:45-07:50  Refresh water bowl                           You         dog_care.md > Adult dog water
  08:15-08:45  Walk                                         You         dog_care.md > Adult dog walks
  10:00-10:15  Brush coat and check ears and nails          You         dog_care.md > Adult dog grooming and baths
  12:00-12:30  Bath day (repeat every four to eight weeks i You         dog_care.md > Adult dog grooming and baths
  17:00-17:30  Walk                                         You         dog_care.md > Adult dog walks
  17:30-17:45  Adult dog meal                               You         dog_care.md > Adult dog feeding
  18:45-19:05  Play or fetch session                        You         dog_care.md > Adult dog play
  critic: 1 revision round(s)
    - round 1: 'Play or fetch session' at 18:30 starts 45 min after the 17:30 meal (wait 60) -> moved
    - round 1: 'Walk' at 07:15 starts 0 min after the 07:00 meal (wait 60) -> moved
```

After: the shared files plus `knowledge/custom/biscuit_notes.md` (vet advice for Biscuit only).

```text
PLAN for Biscuit (dog, adult) on Monday 2026-01-05   confidence 1.00  status: OK
  TIME         TASK                                         WHO         SOURCE
  07:00-07:15  Adult dog meal                               You         dog_care.md > Adult dog feeding
  07:15-07:20  Refresh water bowl                           You         dog_care.md > Adult dog water
  08:30-08:40  Leash-only walk and potty break (vet advice) You         biscuit_notes.md > Biscuit: notes from the vet visit
  10:00-10:15  Brush coat and check ears and nails          You         dog_care.md > Adult dog grooming and baths
  10:30-10:45  Indoor play at home only (vet advice)        You         biscuit_notes.md > Biscuit: notes from the vet visit
  12:00-12:30  Bath day (repeat every four to eight weeks i You         dog_care.md > Adult dog grooming and baths
  14:00-14:10  Leash-only walk and potty break (vet advice) You         biscuit_notes.md > Biscuit: notes from the vet visit
  16:00-16:15  Indoor play at home only (vet advice)        You         biscuit_notes.md > Biscuit: notes from the vet visit
  17:30-17:45  Adult dog meal                               You         dog_care.md > Adult dog feeding
  19:00-19:10  Leash-only walk and potty break (vet advice) You         biscuit_notes.md > Biscuit: notes from the vet visit
  critic: 0 revision round(s)
```

Same request for a different dog (Rex) with the custom file present: nothing changes, because the note is scoped to Biscuit.

```text
PLAN for Rex (dog, adult) on Monday 2026-01-05   confidence 0.98  status: OK
  TIME         TASK                                         WHO         SOURCE
  07:00-07:15  Adult dog meal                               You         dog_care.md > Adult dog feeding
  07:45-07:50  Refresh water bowl                           You         dog_care.md > Adult dog water
  08:15-08:45  Walk                                         You         dog_care.md > Adult dog walks
  10:00-10:15  Brush coat and check ears and nails          You         dog_care.md > Adult dog grooming and baths
  12:00-12:30  Bath day (repeat every four to eight weeks i You         dog_care.md > Adult dog grooming and baths
  17:00-17:30  Walk                                         You         dog_care.md > Adult dog walks
  17:30-17:45  Adult dog meal                               You         dog_care.md > Adult dog feeding
  18:45-19:05  Play or fetch session                        You         dog_care.md > Adult dog play
  critic: 1 revision round(s)
    - round 1: 'Play or fetch session' at 18:30 starts 45 min after the 17:30 meal (wait 60) -> moved
    - round 1: 'Walk' at 07:15 starts 0 min after the 07:00 meal (wait 60) -> moved
```
