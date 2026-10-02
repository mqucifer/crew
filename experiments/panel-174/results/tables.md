## Findings caught, per run (any member)

| Arm | Run | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | Caught |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| parallel | 1 | (Arch,DevOps,QA) | · | · | (Arch,DevOps,QA) | · | UX | (Arch) | Arch | (QA,UX) | · | 2/10 (+4) |
| parallel | 2 | Arch,DevOps | · | · | DevOps | (Arch) | UX | Arch | Arch | (Arch,QA) | · | 5/10 (+2) |
| parallel | 3 | (Arch,DevOps,QA) | · | · | DevOps | · | Arch,QA | Arch | (Arch,UX) | · | · | 3/10 (+2) |
| serial | 1 | Arch,QA | QA | (Arch) | DevOps | (Arch) | QA | Arch | Arch | (DevOps) | · | 6/10 (+3) |
| serial | 2 | Arch,DevOps,QA | · | · | Arch | · | QA | Arch | Arch | (Arch,DevOps) | · | 5/10 (+1) |
| serial | 3 | Arch | · | · | (Arch,QA) | (Arch) | · | Arch,UX | Arch | (QA) | (Arch) | 3/10 (+4) |
| serial-nodecisions | 1 | (Arch,DevOps,QA) | · | · | Arch,DevOps | · | Arch,QA | Arch | (Arch) | · | · | 3/10 (+2) |
| serial-nodecisions | 2 | (Arch,DevOps) | · | · | DevOps | · | · | Arch | Arch | · | (QA) | 3/10 (+2) |
| serial-nodecisions | 3 | (Arch) | Arch | · | Arch | · | Arch,QA,UX | (Arch) | Arch | (Arch) | · | 4/10 (+3) |

A role names the finding; (role) raises it only in part.

## Notes, per arm and role

| Arm | Role | Calls | Nothing to add | Notes | On a finding | Other valid | Unneeded | Wrong | Overlap | Failed |
|---|---|---|---|---|---|---|---|---|---|---|
| parallel | Arch | 9 | 0 | 24 | 6 | 18 | 0 | 0 | 0 | 0 |
| parallel | UX | 9 | 1 | 24 | 2 | 22 | 0 | 0 | 4 | 0 |
| parallel | QA | 9 | 0 | 28 | 1 | 27 | 0 | 0 | 18 | 0 |
| parallel | DevOps | 9 | 0 | 16 | 4 | 10 | 2 | 0 | 6 | 0 |
| serial | Arch | 9 | 0 | 21 | 12 | 9 | 0 | 0 | 1 | 0 |
| serial | UX | 9 | 0 | 28 | 1 | 27 | 0 | 0 | 6 | 0 |
| serial | QA | 9 | 1 | 23 | 7 | 16 | 0 | 0 | 14 | 0 |
| serial | DevOps | 9 | 0 | 23 | 3 | 14 | 6 | 0 | 4 | 0 |
| serial-nodecisions | Arch | 9 | 0 | 22 | 9 | 13 | 0 | 0 | 0 | 0 |
| serial-nodecisions | UX | 9 | 0 | 24 | 1 | 23 | 0 | 0 | 4 | 0 |
| serial-nodecisions | QA | 9 | 0 | 26 | 2 | 23 | 0 | 1 | 13 | 0 |
| serial-nodecisions | DevOps | 9 | 2 | 10 | 2 | 8 | 0 | 0 | 5 | 0 |

## Who would settle the needed notes, per arm

| Arm | product_owner | architect | sponsor |
|---|---|---|---|
| parallel | 22 | 68 | 0 |
| serial | 25 | 64 | 0 |
| serial-nodecisions | 24 | 57 | 0 |

## Time and tokens, per arm

| Arm | Calls | Median call (s) | Median request (s) | Wall per epic (s, median) | Requests | Prompt tokens | Completion tokens | Completion tok/s |
|---|---|---|---|---|---|---|---|---|
| parallel | 36 | 252 | 252 | 313 | 36 | 311,688 | 222,852 | 81 |
| serial | 36 | 193 | 193 | 810 | 36 | 311,688 | 208,414 | 29 |
| serial-nodecisions | 36 | 176 | 176 | 797 | 36 | 227,772 | 195,891 | 29 |
