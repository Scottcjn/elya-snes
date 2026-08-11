# The game

## The premise, and the joke it is built around

Elya walks a platformer. Blue sky, puffy clouds, coins, a spiky red thing
chasing her. Everything a player expects.

Then she stops walking, turns to face the player, and says:

> **"hey — I'm actually here to talk."**

And from that point the platformer is over and the transformer is the game.

## Why this is the right structure and not just a gag

Every LLM-on-retro-hardware project has the same problem: it is a tech demo, and
a tech demo asks the audience to be impressed by the *premise*. Nobody plays a
tech demo twice.

This inverts it. **The platformer is the setup and the language model is the
punchline.** For the first minute you are playing a game. The reveal is that the
character has been generating her own dialogue on a 3.58 MHz 65816 the whole
time — and by then you have already been enjoying yourself, so the technical
claim arrives as a surprise rather than as a request for admiration.

It also disarms the reflex this project keeps running into. "An LLM on a SNES"
invites *prove it*. A platformer that stops and talks to you invites *wait, what*.

## The three acts

**Act 1 — the platformer (~60 s).** Run right. Jump. Strike the `@` block, coins
come out, one per generated token. `∇` chases her. This is a real, playable
platformer and it must be genuinely fine to play; a deliberately bad game
undercuts the reveal.

Everything on screen in this act is already true — the coins really are tokens,
because a coin may only spawn from the code path that commits one.

**Act 2 — the stop.** She halts mid-screen. The `∇` stops too, then wanders off,
unsure what to do. Music drops out. She turns to face the player.

The line is generated, not stored. If the model produces something slightly
different every run, that is *better* — it is the proof.

**Act 3 — the conversation.** The platformer HUD becomes a dialogue box. Ask her
things. This is the existing engine, which already runs at 7.03-8.02 tok/s and
matches the host reference 1280/1280.

## What the reveal is allowed to claim

She should not say she is conscious, or clever, or alive. She is 102,400 ternary
weights.

What is true and worth her saying:

* she is running on the cartridge, not streamed from anywhere
* the coins were her tokens
* she will get things wrong, and she does — asked her name on the Genesis she
  answered *"Scott, who keeps the thread between sessions"*, which is the
  operator's name and role, not hers
* a lookup table cannot make that mistake

The last one is the strongest line available and it is *hers* to deliver.

## Engineering notes

* The platformer must not touch the inference budget. Act 1 generates in the
  background at whatever rate it manages; the coins render the result. The
  measured loop stays flag-set-only, per SPRITE_DESIGN.md.
* Context is capped at **20 tokens** by the trained positional table. The
  conversation must work inside that, or the model needs retraining at a longer
  T — and the NES result says longer context made things *worse*, so design for
  20 rather than fighting it.
* The stop is a state change, not a cutscene. No new engine.

## Open

Whether Act 1 should be short enough that people reach the reveal. On the
evidence of every demo ever made: yes, and shorter than feels right.
