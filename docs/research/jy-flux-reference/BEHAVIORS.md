# Flux Reference Behaviors

The supplied reference is a static dashboard composition. The implementation keeps the same visual behavior while mapping its controls to customer workflows:

- Sidebar items switch between the existing customer workspaces without a full page reload.
- Header search and date controls are represented by the existing customer search/filter controls where the destination page supports them.
- Data cards are actionable: pipeline cards open the related customer filter, follow-up cards open the follow-up workspace, and batch cards open the batch page.
- Hover states are restrained: cards gain a slightly darker border and buttons gain a stronger contrast.
- There is no scroll-driven animation, carousel, or auto-rotating content.
- At tablet width, the sidebar condenses and dashboard cards move into two columns. At mobile width, the sidebar is replaced by the existing bottom action dock and cards stack into one column.

