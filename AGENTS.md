# Project workflow

- Make small, meaningful Git commits after completed and verified milestones.
- Push completed commits to the configured GitHub origin during implementation work.
- Do not commit credentials, local databases, runtime evidence, dependencies, or build output.
- Keep the distinction between the local simulation and a real AWS deployment explicit.
- Run safety tests for recovery-protocol changes and build the frontend after UI changes.
- Treat avoiding AWS out-of-pocket charges as a deployment constraint. Use the minimal demo profile only after checking actual account plan, credits, and shared allowances; do not promise a zero bill or treat budget alerts as a spending cap.
- Keep personal account IDs, notification emails, tokens, and local deployment values in ignored files. Leave consumer polling and replay disabled while the cloud demo is idle.

# Agent skills

- Apply `karpathy-guidelines` to implementation and review: state assumptions, keep changes small, avoid speculative abstractions, and define verifiable success criteria.
- Use relevant installed ECC skills for each task, reading their `SKILL.md` instructions first. Select only skills that fit the work rather than loading the entire collection.
- Use Graphify for project architecture and dependency mapping; query an existing graph before rebuilding it. Exclude secrets, ignored runtime data, dependencies and build artifacts. Mapping must not restart, rebuild or otherwise disturb the running application.
