# Overseer

When running agentic coding flows, it can be hard to tell what is changing,
why, and what impacts it may have on the system as a whole.  It can be hard to
tell if the changes are well-architected, re-using pre-existing functionality,
and are behaving as intended.

What if there was a way to get more than just line diffs?

This project aims to help every engineering change answer these questions:

1. What changed mechanically?
2. What changed semantically?
3. Why was the change made?
4. What system parts are impacted by the change?
5. How does the change impact user-observable behavior?
6. Does this change follow existing patterns?
7. Should the code be refactored?
8. How has test coverage been impacted?

Some changes are easier than others to reason about, but in 2026 it's no longer
viable to simply review git diffs and say "the code passed code review".
