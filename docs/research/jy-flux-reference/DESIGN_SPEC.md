# Flux-Inspired Customer Dashboard Spec

## Visual foundation

- Outer canvas: pale lime `#dff08a`.
- Application shell: near-black outline `#17171b`, dark rail `#202024`, pale gray workspace `#e9e9e7`.
- Cards: white `#ffffff` with `#dcdcdc` borders and 20-24px radius.
- Accent: lime `#d9f34f`; supporting data accent: soft violet `#b8a8ee`.
- Typography: `Inter`, `Geist`, or system sans; Chinese fallback `PingFang SC`.
- Buttons: black filled pills for primary actions, white outlined pills for secondary actions.

## Desktop structure

- Shell max width approximately 1450px, centered on the olive canvas.
- Dark rail 190-210px wide.
- Workspace padding 22-30px.
- Dashboard uses a 12-column card grid rather than a single hero card.
- Cards align to a common grid and use a shared heading block: title, muted description, compact control.

## Data mapping

- Contribution history -> customer pipeline stages using straight vertical bars.
- Payout threshold -> today's follow-up count and next action.
- Savings targets -> account opening, intention, and participation completion.
- Sleep analysis -> confirmed participation amount and batch progress.
- Recent transactions -> recent customer updates.

## Responsive rules

- `<=1180px`: two-column dashboard grid, compact rail.
- `<=620px`: one-column cards, hidden desktop rail, fixed bottom actions.
- Charts never skew or rotate; bars are vertical, equal-width, and aligned to a visible baseline.

