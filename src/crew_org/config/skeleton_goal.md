Goal: a walking skeleton, so every check this project's design names runs and passes
---
This project has a record and a design, and no code its checks can run yet. Before any other work, it gets the thinnest version of itself that its own checks run on and pass: a walking skeleton (Alistair Cockburn; Freeman and Pryce, *Growing Object-Oriented Software, Guided by Tests*). It isn't the project's final shape: the first real stories replace it.

**The outcome:** every check the design names runs and passes, in the crew's sandbox and in CI on every pull request. The design's setup installs everything those checks use, from the lockfile the design names, which is committed.

The skeleton holds only what the design's structure names, as placeholders. It doesn't deploy: the first real epic makes the first release.

**Sponsor decisions:**

| ID | Decision | Consequences |
|---|---|---|
| D1 | Only what makes the design's checks run and pass. Its first story does that alone, before any other story | Until it lands, every other story would fail checks that have nothing to run. The project's pages, features and behaviour belong to its own Goals |
| D2 | Each placeholder file carries the marker `crew:placeholder` in a comment | The story that replaces a placeholder needs no declared contract change for it |
| D3 | Nothing the design's structure doesn't name | The skeleton isn't the architecture; the first stories are |
| D4 | CI runs the design's setup and every check on each pull request | The Sponsor then requires those checks on the default branch, and the project is ready for any Goal |
| D5 | No deploy | The first real epic makes the first release |

---

_The standard first Goal for a new project, filed by `crew onboard` on the Sponsor's yes (ADR 0030)._
