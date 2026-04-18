# Spec: UI Tools and Resources

## Purpose

Capture external design tools and how we use them so future projects do not need repeated discovery work.

## Tool: Stitch

Source: https://stitch.withgoogle.com/

### What it is

Stitch is a Google Labs experimental tool that transforms natural language prompts into high-fidelity UI design concepts.

### How to use in our workflow

- Use Stitch in early discovery and layout ideation.
- Validate information hierarchy and interaction flow from generated concepts.
- Implement final UI in codebase-native components instead of relying on direct tool export.

### Constraints

- Treat as experimental product behavior; features may change.
- Do not assume stable, production-ready API integration.
- Keep design decisions documented in project specs before implementation.

### Reusable prompt pattern

- Product context: target users, key scenario, platform.
- Page goal: what decision/action the user should complete.
- Content modules: cards, filters, stats, timeline, tables.
- Visual direction: tone, color logic, typography style.
- Interaction states: loading, empty, error, success.
- Responsiveness: desktop and mobile expectations.

### Reuse checklist for implementation

1. Define UX objective and success metric.
2. Draft concept in Stitch.
3. Convert concept into component structure.
4. Add state handling (empty/error/loading).
5. Run visual polish pass (spacing, contrast, hierarchy).
6. Record decisions in project spec.

## LinHelp reference

Applied in enhancement simulator redesign:

- stitch-inspired visual hierarchy
- panel-based workflow
- quick simulation controls with outcome visualization
