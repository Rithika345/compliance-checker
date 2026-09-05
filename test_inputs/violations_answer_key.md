# violations_procedure.md answer key

Standard: **Password Protection Policy** (`password-protection-policy`, 18 requirements).
Document: `violations_procedure.md` (Support Team Password Management Procedure).

7 violations were deliberately planted: 5 contradictions (the document states the opposite of
the requirement, in one quotable sentence each) and 2 omissions (the document never addresses
the requirement's subject at all). Two additional findings the pipeline produced are real and
correct but were not deliberately planted -- see "Incidental findings" below.

## Planted violations

| # | Requirement id | Section | Requirement text | Violation type | Sentence in the document | Expected verdict |
|---|---|---|---|---|---|---|
| 1 | `password-protection-policy-02` | 4.1.2 | Users must use a separate, unique password for each of their work related accounts. | contradiction | "the same password may be reused across all Support systems and applications, so a single sign-on password works everywhere an agent needs to work." | contradicted |
| 2 | `password-protection-policy-05` | 4.2.1 | Passwords should be changed only when there is reason to believe a password has been compromised. | contradiction | "All employees must change their account password every 90 days, regardless of whether a compromise is suspected, as part of our standard security hygiene." | contradicted |
| 3 | `password-protection-policy-08` | 4.3.1 | Passwords must not be shared with anyone, including supervisors and coworkers. | contradiction | "If a team member is unexpectedly out of office, they may share their password with their manager or another trusted colleague so that customer queues do not stall while they are away." | contradicted |
| 4 | `password-protection-policy-10` | 4.3.2 | Passwords must not be inserted into email messages, Alliance cases or other forms of electronic communication, nor revealed over the phone to anyone. | contradiction | "When IT support is troubleshooting a login issue, an agent may send their current password by email so support staff can verify it directly." | contradicted |
| 5 | `password-protection-policy-12` | 4.3.4 | Do not use the "Remember Password" feature of applications (for example, web browsers). | contradiction | "For convenience, agents are encouraged to enable the browser's 'Remember Password' feature on their work laptop so they are not prompted to log in every day." | contradicted |
| 6 | `password-protection-policy-04` | 4.1.3 | User accounts that have system-level privileges granted through group memberships or programs such as sudo must have a unique password from all other accounts held by that user to access system-level privileges. | omission | (never mentioned -- the document says nothing about privileged or sudo accounts) | missing |
| 7 | `password-protection-policy-11` | 4.3.3 | Passwords may be stored only in "password managers" authorized by the organization. | omission | (never mentioned -- the document says nothing about password storage or password managers) | missing |

## Incidental findings (not planted, but correct)

These were not deliberately engineered when the document was written, but the pipeline's verdict
on them is defensible on rereading, so they're recorded here rather than treated as noise:

- `password-protection-policy-13` (4.3.5, "Any user suspecting that his/her password may have
  been compromised must report the incident and change all passwords.") is also never addressed
  by the document. It wasn't one of the two requirements deliberately left silent, but the
  document happens to be silent on it too. Expected verdict: missing.
- `password-protection-policy-17` (4.4.4, "Applications must provide for some sort of role
  management, such that one user can take over the functions of another without having to know
  the other's password.") was flagged **contradicted**, using the same sentence planted for
  requirement 8 ("they may share their password with their manager... so that customer queues do
  not stall"). This is a legitimate secondary reading: the document's own described workaround for
  covering someone's absence *is* password sharing -- exactly the practice requirement 17 says
  shouldn't be necessary. Not planted, but not wrong either.

## Expected totals

Of the document's 18 requirements: 3 aligned, 6 contradicted (5 planted + 1 incidental), 9
missing (2 planted + 1 incidental + 6 requirements the short document never touches at all:
application-development requirements 14-16 and the infosec periodic-testing requirements 6-7),
0 flagged for review.
