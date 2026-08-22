# PVE user guide

## Main areas

- **Home** shows readiness, saved material, failures, and common actions.
- **Study** and the task pages create a plan for one module.
- **Full Study Pack** includes research, notes, revision, quiz, viva practice, and flashcards.
- **My Materials** searches saved work locally by module, keyword, and type.
- **Progress** and **System Status** explain local health without contacting providers.
- **Help** answers common questions in plain language.

## Creating material

Choose a module, task, and depth. PVE then shows a non-consuming preview: outputs, expected AI services, bounded request estimate, cost status, and reusable work. **Unknown cost is shown as Unknown, never zero.** Review that preview before running.

You may also type an ordinary request such as “Create detailed notes and a 20-question quiz for PID control.” PVE parses this locally into a validated structured command. Ambiguous, unbounded, destructive, provider-switching, and budget-bypassing requests do not execute.

## Failures and recovery

PVE preserves completed work and provider evidence. A failure explanation tells you what happened, whether an external request occurred, whether an automatic retry occurred, and the safe next step. An ambiguous request is never automatically resubmitted. Use **Resume Safely** only when the controller offers it.
