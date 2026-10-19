# README terminal demo

`filo-demo.gif` shows Filo's actual Textual interface with a scripted synthetic Italian
conversation. It is an illustrative UI demonstration, not a recording of live model output or
measured response latency. No provider was called, no user data was read, and the capture used a
temporary isolated store. The application code and default terminal theme were not modified.

## Transcript

**You:** Continuo a rimandare un progetto a cui tengo.

**Filo:** Quando pensi di iniziare, qual è la parte che ti blocca?

**You:** Vorrei farlo bene subito, così non comincio mai.

**Filo:** Da quello che dici, sembra che la prima bozza debba già essere quella buona.
Ti andrebbe di provare dieci minuti su una versione volutamente imperfetta?

## Capture details

Created on 2026-10-19 against Filo commit `9672ebb` using `FiloApp.run_test(size=(92, 30))`
and Textual's SVG screenshot export, with `NO_COLOR` unset. The sequence uses real user and
assistant widgets, incremental scripted text, and readable pauses. HyperFrames 0.8.174 rendered
it at 12 fps; gifsicle optimized the GIF to 960 pixels wide and 128 colors. The static PNG is
a decoded frame from the finished GIF. The original synthetic copy and media are provided under
the repository's AGPL-3.0-or-later terms.
