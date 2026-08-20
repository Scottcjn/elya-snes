#!/usr/bin/env python3
"""mkgame.py -- the tables the game layer needs that the model already implies.

Two things:

  vocab.tbl   64 entries of [len][up to 5 ASCII bytes].  The model's symbols are
              BPE pieces, not characters -- ' the ' is ONE token -- so printing
              a token means printing a string, and the ROM needs that string
              table to show what it generated.

  the questions.  Act 3's menu is stored (the player's question is input, not
              output) and is tokenised HERE against the same vocabulary, so
              what the cartridge feeds the model is exactly what the host
              reference would feed it.  The ANSWER is generated on the console;
              tools/check_game.py re-runs host/ref.py over these same prompts
              and requires the cartridge to match it token for token.

Context is 20 positions, full stop -- it is what the positional table was
trained with.  A question that costs 10 tokens leaves 10 for the answer, so the
menu is chosen short on purpose; `assert len(p) <= MAXPROMPT` is the whole
policy.
"""
import json
import os
import struct
import sys

MAXPROMPT = 10          # leaves at least half the context for her answer
NCTX = 20

# The menu.  Filtered by train/pick_menu.py against the shipped weights: every
# question below is inside the ten-token prompt cap AND host/ref.py, decoding
# the way rom/game.inc does, reproduces the corpus answer exactly.  The six
# were then chosen for spread - one per topic - and for what they let her say.
#
# 'who are you?' earns its place by being the question everyone asks.  It used
# to earn it by getting a WRONG answer, which was the honest half of the
# demonstration on the TinyStories model; she now gets it right, and the honest
# half moved to 'are you a table?' - she answers 'no. i can err.', which is
# both true and the only defence she has.
#
# 'can i trust you?' was here and is not any more.  Not because the answer
# changed - it is still 'check the coins.' - but because the paraphrase corpus
# refitted the vocabulary and the same fourteen characters now cost ELEVEN
# tokens instead of ten, one over the cap.  'why trust you?' is ten, is in the
# corpus, and gets the same answer.  Token cost is a property of the corpus,
# so the menu has to be re-checked whenever the corpus moves.
QUESTIONS = [
    "who are you? ",        #  6 tokens -> 'i am elya.'
    "what are you? ",       #  5        -> 'a small model.'
    "the coins? ",          #  4        -> 'one is a token.'
    "why stop? ",           #  7        -> 'i want to talk.'
    "are you a table? ",    # 10        -> 'no. i can err.'
    "why trust you? ",      # 10        -> 'check the coins.'
    # These two exist so every shard is REACHABLE.  The first six questions
    # route onto four of the six shards, and the hardware and history models
    # were only ever exercised through the SHARD0 boot override -- weights on
    # the cartridge that no button could reach.  The menu draws one question
    # at a time, so two more entries cost nothing but these bytes.
    "what console? ",       #  7        -> 'the snes.'        (hardware)
    "snes maker? ",         #  9        -> 'nintendo.'        (history)
]


def encode(vocab, s):
    """Longest-match tokenise.  Verified by round-trip below, which is the only
    property that matters: what the ROM prints must be what was asked."""
    out, i = [], 0
    while i < len(s):
        best = None
        for j, t in enumerate(vocab):
            if s.startswith(t, i) and (best is None or len(t) > len(vocab[best])):
                best = j
        if best is None:
            raise SystemExit("cannot tokenise at %r" % s[i:])
        out.append(best)
        i += len(vocab[best])
    assert "".join(vocab[t] for t in out) == s, "round trip failed for %r" % s
    return out


def main(outdir):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    vocab = json.load(open(os.path.join(root, "data", "vocab.json")))["vocab"]
    assert len(vocab) == 64

    os.makedirs(outdir, exist_ok=True)

    tbl = bytearray()
    for t in vocab:
        b = t.encode("ascii")
        assert len(b) <= 5, "vocab entry %r is longer than the table row" % t
        tbl += bytes([len(b)]) + b + bytes(5 - len(b))
    open(os.path.join(outdir, "vocab.tbl"), "wb").write(tbl)

    # THE SHARD PER QUESTION.  Act 3's menu is six fixed questions -- the
    # player picks, they do not type -- so which topic each one belongs to is
    # known at BUILD time and the cartridge needs no runtime router at all.
    #
    # That matters more than it sounds.  train/router.py measures the shipping
    # word-counts router at 68.6% on dev+test, and that number is the price of
    # FREE-TEXT input.  This cartridge has no free-text input, so a table
    # lookup routes the menu at 100% and costs six bytes.  Building the
    # classifier in 65816 would have shipped worse routing than doing nothing.
    #
    # It is derived from train/corpus.py rather than written down here, so a
    # question that moves topic cannot leave the table pointing at the old one.
    sys.path.insert(0, os.path.join(root, "train"))
    import corpus as C                                            # noqa: E402
    owner = {}
    for topic, _ans, d in C.FACTS:
        for sp in ("train", "dev", "test"):
            for qq in d[sp]:
                owner[qq] = topic
    shards = bytearray()
    for q in QUESTIONS:
        if q not in owner:
            raise SystemExit("%r is not in train/corpus.py, so its shard is "
                             "unknown" % q)
        shards += bytes([C.TOPICS.index(owner[q])])
    open(os.path.join(outdir, "qshard.bin"), "wb").write(shards)

    toks, offs, lens = bytearray(), bytearray(), bytearray()
    for q in QUESTIONS:
        p = encode(vocab, q)
        if len(p) > MAXPROMPT:
            raise SystemExit("%r is %d tokens, over the %d-token budget"
                             % (q, len(p), MAXPROMPT))
        offs += struct.pack("<H", len(toks))
        lens += bytes([len(p)])
        toks += bytes(p)
        print("%-16r %2d tokens, %2d left for the answer, shard %d (%s)"
              % (q, len(p), NCTX - len(p),
                 C.TOPICS.index(owner[q]), owner[q]))
    open(os.path.join(outdir, "qtok.bin"), "wb").write(toks)
    open(os.path.join(outdir, "qoff.bin"), "wb").write(offs)
    open(os.path.join(outdir, "qlen.bin"), "wb").write(lens)

    with open(os.path.join(outdir, "gdata.inc"), "w") as f:
        f.write("; GENERATED by tools/mkgame.py -- do not edit\n")
        f.write("NQUEST      = %d\n" % len(QUESTIONS))
        f.write("QTOK_BYTES  = %d\n" % len(toks))
        f.write("MAXPROMPT   = %d\n" % MAXPROMPT)
        f.write("NSHARDTOPIC = %d\n" % len(C.TOPICS))
    print("vocab table %d bytes, %d questions, %d prompt tokens"
          % (len(tbl), len(QUESTIONS), len(toks)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "out/game"))
