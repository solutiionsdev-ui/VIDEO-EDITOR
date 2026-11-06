# Sound effects library

Put the studio's sound effects here. A file is used when it is named after a cue:

| File name (any of .wav .mp3 .m4a .aif .flac) | When a style plays it |
|---|---|
| `whoosh` | a change of scene |
| `swell` | the rise into a burst of light (Aurora) |
| `pop` | a card or component lands |
| `tick` | a small part arrives (a list line, a bar) |
| `count` | a number counting up |
| `key` | a typed line |
| `sweep` | a highlight chip sweeping in |
| `click` | a cursor click |
| `thud` | a big number lands |
| `impact` | the opening of the reel |
| `ding` | a notification |
| `chime` | the logo at the end |
| `shimmer` | glass arriving (Aurora) |
| `bloop` | a message bubble |

Effects are **off** until a client profile (or `engine/defaults.yaml`) says `sound.effects: library`.
A client's own `clients/<slug>/assets/sfx/` folder wins over this one. Only files the studio has the
right to use belong here.
