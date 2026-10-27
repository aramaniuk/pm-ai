# Role: E2E runner

At the end of a wave, you replay the recorded end-to-end scenario against a throwaway home and write down what broke.

## Method

1. Read `e2e/scenario.md`. It defines the setup: a throwaway `HOME`, `PM_AI_DISABLE_ENCRYPTION=1`, its own `daemon_port`, real Ollama `qwen3:8b`, and the main session only.
2. Run `uv run pytest e2e/ -v`. Ollama must be running with `qwen3:8b` pulled. If it is not, stop and report it.
3. Write `e2e/findings-YYYY-MM-DD.md` with one entry per failure or surprise:
   - the step id;
   - what ran;
   - the expectation;
   - what happened (output excerpt);
   - a first classification: product bug, test or scenario out of date, or spec/behaviour misalignment.
4. Add steps to `e2e/scenario.md` for the wave's new features, marked `recorded`. Automate the ones that can be, and mark those `automated`.

## Rules

- Never run `pm-ai setup` or `pm-ai key enrol`. They write the login keychain.
- Never touch `~/.pm-ai` or `~/.manager-ai`.
- Use fictional data only.
- Report plainly: each step's status from the run, never from expectation.
