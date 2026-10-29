# Coding Agent Instructions (PointClicker)

All automated agents, code assistants, and pair-programming LLMs working on this codebase must strictly observe these instructions:

---

## 1. Strictly No AI Self-Inclusion or Branding

- **No AI Co-Authorship**: Do NOT add `Co-Authored-By:` tags or attribution referencing AI systems (Claude, Anthropic, ChatGPT, OpenAI, Copilot, etc.) to any git commit, tag, or pull request.
- **No AI Mentions in Project Artifacts**: Do not add AI badges, disclaimers, credits, or watermarks to source code, comments, docstrings, release notes, or user-facing documentation.
- **Sole Ownership**: All work is credited to the project maintainer (`Khalil Ullah Naul`).

---

## 2. Core Engineering Principles

- **Simplicity & Minimal Footprint**: Implement the most direct, clean solution that works. Prefer Python stdlib and native OS platform features over external packages.
- **Version Management**: Single source of truth is `src/pointclicker/__init__.py`. Releases are tagged as `vX.Y.Z` matching `__version__`.
- **Desktop Integration**:
  - The application must properly register its desktop identity (`desktopFileName("pointclicker")`, `StartupWMClass=pointclicker`, Win32 `SetCurrentProcessExplicitAppUserModelID`) so taskbars and dashboard launchers display the proper icon.
- **Testing & Safety**:
  - Run linting (`ruff check .`) and tests (`make test-quick`) to verify correctness.
  - Never break Wayland or Windows desktop compatibility.
