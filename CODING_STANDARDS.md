# Coding standards

Apply these standards when writing, changing, or reviewing code in this
repository.

See the [development guide](docs/development.md) for commands and checks.

## Design and scope

- Make the smallest change that solves the stated problem.
- Trace callers before changing shared behavior.
- Keep related behavior in one module. Prefer a small interface that hides
  validation, error handling, and I/O details from callers.
- Reuse existing code and the standard library before adding
  a dependency or abstraction.
- Remove obsolete code and tests when their replacements cover the same
  behavior. Explain non-obvious constraints in comments; avoid narrating code.

## Tests and completion

- Don't write tautological tests. Only test behaviour.
- Prefer explicit dependencies and controlled fixtures over patching or mocking.
- For a behavior bug, first add a focused regression test and confirm that it
  fails for the reported reason. Then make the smallest fix and run it again.
- Test public outcomes rather than private call sequences. Use controlled
  fixtures for content rules and clean up temporary files.
- For documentation-only changes, verify links, examples, and diff whitespace;
  a full runtime suite is not required. Do not add tests for trivial deletions
  that existing checks already cover.
