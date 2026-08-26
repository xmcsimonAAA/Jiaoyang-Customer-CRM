# JY Customer Workspace Design Spec

## Direction

The interface follows the supplied finance dashboard reference without copying its brand or content. It uses a quiet gray canvas, a large white application shell, a light navigation rail, one near-black primary data card, and soft mint, peach, and powder-blue metric surfaces.

## Tokens

- Canvas: `#f1f1f0`
- Surface: `#ffffff`
- Primary ink: `#17171c`
- Muted ink: `#85858d`
- Divider: `#ececef`
- Positive / progress: `#1db694`
- Mint surface: `#e4f6e9`
- Peach surface: `#fff0e7`
- Powder surface: `#eaf0fa`
- App shell radius: `28px` desktop, `0` mobile
- Panel radius: `13px` to `17px`

## Layout

- Desktop uses a 218px navigation rail and a fluid content area.
- The dashboard uses a fluid main column and a 274px action column.
- Below 1180px, the action column moves beneath the main dashboard.
- Below 620px, the sidebar is replaced by a fixed three-action mobile dock.
- Tables remain horizontally scrollable because column-level editing is an essential workflow.

## Components

- Primary commands use near-black filled buttons.
- Secondary commands use white buttons with subtle gray borders.
- Status values use small square tags instead of oversized pills.
- Dashboard metrics use category color only on their data surfaces.
- Forms use 44px controls for touch accessibility.
- Modals switch to one-column forms on mobile.

