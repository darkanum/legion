# Legion brand

The marks of Legion and its three daemons, in the Vapulab identity. Everything here except this file, `fonts/`,
`build.py` and `examples.py` is output: change `build.py`, then run `python brand/build.py` from the repository root (it needs fonttools,
brotli, uharfbuzz and Chrome or Edge for the PNGs, and it fails when a colour drops below WCAG AA).

## The system

Vapulab names its internal engines after daemons of the Ars Goetia whose office matches what the software does, and
draws each one as a **seal**: a sigil in line inside a ring, one colour per daemon. The name is set in Martian Mono,
the office below it in capitals. Legion is the summoning circle that holds the three seats.

| Daemon | Office in the Goetia | Sigil | Dark | Light |
| --- | --- | --- | --- | --- |
| **Marbas** | cures diseases and teaches the mechanical arts | a fracture stitched shut, rising to the node where the cure takes hold | `#2DD4BF` | `#0F766E` |
| **Valac** | reveals hidden treasures and where the serpents lie | the serpent in the grass, and the ring that finds its head | `#FDE047` | `#A16207` |
| **Orobas** | answers truly of things past, present and to come | the brackets of a review, and the check that passes only when true | `#F472B6` | `#BE185D` |
| **Legion** | the circle that holds them | a triangle in a ring, one seat per daemon in its colour | `#EDF0F4` | `#090D14` |

*Dark* is the colour on the ink surface `#0A0F1A`; *light* is the deeper tone for the bone surface `#FAFAF9`.

## Files

- `logos/<name>/mark-{dark,light}.svg`: the seal alone, 64×64.
- `logos/<name>/lockup-{dark,light}.svg`: the seal with the name in curves, so no font needs to be installed.
- `icons/<name>.svg`: a favicon that follows the system theme; `icons/<name>-512.png`: an app icon.
- `covers/<name>.png`: a 1200×630 social card per daemon; `covers/legion.png`: the 1280×640 repository card.
- `tokens.json`: the neutrals, the agent colours and the fonts.
- `examples/*.png`: the interactions in the README, from `python brand/examples.py`. That script replays the host
  code with Slack and GitHub stubbed (it needs `gh` signed in, for GitHub's Markdown renderer), so the images change
  when a message format does. Run it after any change to `marbas/host/listener.py`, `orobas/host/report.py` or
  `valac/host/issue_text.py`.

The fonts are under the SIL Open Font License 1.1 (`fonts/OFL-*.txt`).
