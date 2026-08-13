# AI gags

The rule for all of them: **the joke has to be true.** Every gag below is
either something the model actually does, or something the hardware actually
imposes. Nothing here is a caption pasted over a static screen — on this project
the whole argument is that the machine is really doing the work, and a fake gag
undermines it faster than no gag at all.

## Shipped, unplanned, still the best one

Asked `What is your name?:` the Genesis ROM answered:

> **Scott, who keeps the thread between sessions.**

Fluent, grammatical, topically adjacent, and wrong — that is the operator's name
and role, not hers. It is funny *and* it is the strongest evidence in the project
that this is not a lookup table. Lookup tables do not misremember. They hit or
they miss. Only real inference fails by confidently naming the wrong thing.

### And then the SNES did it again, independently

Different hardware, different corpus, different training run. Asked `who is
this?` — a phrasing of "who are you?" the model was never trained on — the SNES
cartridge answers:

> **scoty maker.**

`scott` and `my maker` collapsed into a single word. The corpus contains both
strings and neither of them is that. It is the same failure in a smaller
vocabulary, and it reproduces on demand:

```sh
python3 train/eval_answers.py model/elya_qa_s2.npz --all | grep "who is this"
```

### And it survived the corpus that fixed her

Entry 11 tripled the corpus and the collision gag did not go away with the
memorisation. `model/elya_qa_para_s2.npz` answers all 208 of its training
phrasings exactly, and on held-out paraphrases it still fuses two answers into
a word that is in neither of them:

```sh
python3 train/eval_answers.py model/elya_qa_para_s2.npz --split test --all
```

```
what exactly?       ->  a smyes. often.     ('a small model.' + 'yes. often.')
by whom?            ->  all mowenty tokens.
what is after you?  ->  ascott did.
you recall?         ->  no. itweights.
which console?      ->  sip.
the spike?          ->  s? the snes.
```

`sip.` is the best of them. Asked which console she runs on — a phrasing she
was never trained on — she emits four characters that are not a word and are
not in the answer she wanted (`the snes.`), and then stops. Whatever the
network was reaching for, it was not a string it had.

And she misspells while free-running: `do you ream?`, `neeed ram?`, `kep going.`, `very litle
of me thinks.` **None of these are authored.** Nothing in `train/corpus.py`
contains a stutter or a misspelling; every one of them is 102,400 ternary
weights getting it slightly wrong on a 3.58 MHz 65816, and every one is
visible on screen without a caption explaining it.

## The idle loop: she taps her foot

When the ROM is not generating, Elya taps her foot.

This is the AI-waiting-on-you gag inverted — every other product makes you watch
a spinner while the model thinks. Here the model waits on **you**, visibly, with
increasing impatience.

Escalation (all token-count driven, never timers):

| idle time | behaviour |
|---|---|
| 0-5 s | idle bob |
| 5-15 s | foot tap starts |
| 15-30 s | tap speeds up, arms fold |
| 30 s+ | she looks at the player, then at the `@` block, then back |

**Cost: zero.** It only runs when nothing is being generated. The measured
inference path is untouched, which is the whole point — see SPRITE_DESIGN.md.

Implementation: `~/legend-of-elya-genesis/train/make_intro.py` already converts
an mp4 into a streaming 16-bit Genesis animation (128x96, 12 fps, tile-based,
DMA'd in vblank). A short loop is a much smaller job than the existing intro:
about 36 frames for a 3 s cycle against the intro's 414 KB.

## Gags that are true

* **`@` block.** She strikes a block stamped `@` to produce tokens. `@` is the
  matrix-multiplication operator in Python and numpy. The block is stamped with
  the operation that produces what comes out of it.

* **`∇` chases her.** Nabla is the gradient operator and is already a spiky
  triangle. Gradient descent, literally descending. The second layer: the ROM
  does *inference*, so there are no gradients in it at all — ∇ is the thing from
  training that cannot touch her any more, still chasing anyway.

* **The coin counter is the token counter.** Not a decoration. One coin per
  committed token, spawned from the same code path. A viewer can pause the video
  and check coins against characters without trusting anything the ROM prints.

* **She is slower on a real console than in the emulator, and says so.** The
  Genesis ROM prints its own tok/s from its own vblank counter. On hardware it
  reads 1.77-2.00 because the rate is quantised to 60/k. A status line reading
  `honest wall clock` is already in the source.

* **The `.sav` file.** The NES port has no video output at all; it writes
  generated tokens into battery-backed SRAM. The gag writes itself: a cartridge
  that appears completely dead and has been quietly thinking the whole time.

## Gags to avoid

* "As a language model, I cannot..." — she is 102,400 ternary weights on a
  cartridge. The joke belongs to a different kind of model and it is not true here.
* Any answer longer than the context. Question plus answer is TWENTY TOKENS,
  full stop — `rom/game.inc` feeds the question from position 0 and generates
  exactly `20 - len(prompt)` tokens. A line that does not fit is not a line she
  can say; `train/prep_qa.py` refuses to build a corpus containing one.
* Anything implying she is smarter than she is. The model is small and the
  corpus is saturated; the article says so. A gag that oversells contradicts the
  paper.
* Any joke that requires a caption to land. If it needs explaining it is a
  caption, not a gag.

## Recording

**MAME records the ROM directly — no screen capture needed.** Verified:

    mame genesis -cart <rom>.bin -sound none -seconds_to_run <n> \
         -nothrottle -skip_gameinfo -video soft -window \
         -aviwrite /home/scott/mame/snap/out.avi

⚠️ `-video none` produces no file. A video backend must be active.
⚠️ Output must be an explicit absolute path or it lands somewhere unhelpful.
⚠️ Uncompressed: ~10 MB/s. Re-encode before uploading anywhere.
