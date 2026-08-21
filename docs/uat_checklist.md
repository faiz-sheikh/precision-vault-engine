# Startup MVP UAT Checklist

- [ ] `pve doctor` reports SQLite integrity OK.
- [ ] All provider switches are off and page refresh creates no ledger event.
- [ ] Candidate manifest validates; promotion remains blocked pending fresh approval.
- [ ] Duplicate command click returns the same command ID.
- [ ] Mocked run one calls expected providers; identical runs two and three call none.
- [ ] Parser crash resumes raw checkpoint without provider recall.
- [ ] Ambiguous acceptance cannot resubmit.
- [ ] Stale worker commit is rejected.
- [ ] Backup restore check passes.
- [ ] Secret scan and Git diff review pass.
