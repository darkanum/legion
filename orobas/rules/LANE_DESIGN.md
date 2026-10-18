# Lane: design

The build matches the design and the design system. When the issue or the PR links a design, compare against it;
when it does not, compare against the design system and the screens around the change.

## DES-01 The design system is used

The change uses the project's existing components, tokens and styles (colours, spacing, type, radii, shadows) instead
of new one-off values or copies of an existing component.
**Default:** important.

## DES-02 The build matches the design

Layout, hierarchy, copy and states match the linked design. A deliberate difference is said in the PR body.
**Default:** important.

## DES-03 Every state is designed

Loading, empty, error, disabled, hover, focus and long-content states exist and look intended, not left to the
browser default.
**Default:** important.

## DES-04 It works at every size

The view holds at phone, tablet and desktop widths: no horizontal scroll, no overlapping or clipped content, touch
targets large enough.
**Default:** important.

## DES-05 It works with a keyboard and a screen reader

Every control can be reached and used with the keyboard, has a visible focus state and an accessible name. Images
have alt text, form fields have labels, and structure uses real headings, lists and buttons.
**Default:** important; blocker when a main action cannot be reached without a mouse.

## DES-06 Contrast and colour are readable

Text and icons meet contrast guidelines against their background, in every theme the project supports, and colour is
never the only signal.
**Default:** important.

## DES-07 Motion is calm and optional

Animation is short and purposeful and respects the user's reduced-motion setting.
**Default:** note.

## DES-08 Assets are right-sized

Images and icons are in a suitable format and size, with dimensions set so the layout does not jump while they load.
**Default:** note.
