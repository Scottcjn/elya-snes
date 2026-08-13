#!/usr/bin/env python3
"""Where the router fails, before anything is changed.

FINDINGS's routing entry gives one number -- 27.5% test error -- and a number
is not a diagnosis.  This file splits the held-out errors into the two causes
that need different fixes:

  OOV / default   the question's words carry NO weight at all, or the winning
                  margin is zero, so `argmax_low` returns topic 0.  That is a
                  VOCABULARY failure: the router never saw the word.  It shows
                  up in the confusion matrix as a column-`identity` sink,
                  because identity happens to be TOPICS[0].
  confused        the score was decisive and decided wrong.  That is either a
                  weighting failure (one common word outvoting the topical one)
                  or genuine ambiguity.

and then names the single word most responsible for each confused error, so
"one stopword is doing this" can be checked rather than assumed.

--residual answers the other question: of the errors that are LEFT after the
router is as good as this corpus can make it, how many are the router's fault
at all?  It refits leave-one-out over all 345 corpus questions -- so the router
has seen every other phrasing of every fact, which is the most any amount of
routing work could ever give it -- and splits what still fails into

  vocabulary hole   every content word of the question occurs exactly once in
                    the whole corpus, which is to say only in this question.
                    A router cannot weight a word it has never seen.  The fix
                    is a training phrasing that uses the word, and that is a
                    corpus change.
  ambiguous         the content words ARE in the corpus, under other topics,
                    or there are no content words at all.  `who am i? ` reads
                    as identity to anything that reads words; it is filed
                    under honesty because its ANSWER is `no. i forget.`  No
                    feature over the question can recover that.
"""
import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import corpus as C
import router as R


# Function words, for splitting "the router never saw this word" from "the
# router saw it and it carries no topic".  Written out rather than derived,
# because a frequency cutoff on a 345-question corpus would put `dream` on the
# list.
STOP = set("""a an and are as at be by can do does did for from has have how i
is it me my no not of on or so that the there this to us we what when where
which who why will with you your yours am any all still now then more""".split())


def residual():
    """Leave-one-out over the whole corpus, and why what is left is left."""
    allrows = [(t, q, a, s != "train") for t, q, a, s in C.qa_rows()]
    held = {q for _t, q, _a, _h in C.rows_of("dev") + C.rows_of("test")}
    freq = collections.Counter()
    for _t, q, _a, _h in allrows:
        for w in R.words(q):
            freq[w] += 1

    print("leave-one-out over all %d corpus questions, %s -- the router is\n"
          "refitted without each question and then asked to route it, so it\n"
          "has seen every other phrasing of every fact."
          % (len(allrows), "wordgram-lr"))
    wrong, nok = [], 0
    for i, (t, q, a, _h) in enumerate(allrows):
        rest = [r for j, r in enumerate(allrows) if j != i]
        W = R.fit_lr(rest, R.wordgrams)
        p = C.TOPICS[R.argmax_low(R.score(q, W, R.wordgrams))]
        if p == t:
            nok += 1
        else:
            wrong.append((q, t, p, a))
        if (i + 1) % 50 == 0:
            print("   %3d/%d refits, %d correct so far"
                  % (i + 1, len(allrows), nok), flush=True)
    print("\nLOO %d/%d = %.1f%% over the whole corpus"
          % (nok, len(allrows), 100 * nok / len(allrows)))

    hw = [w for w in wrong if w[0] in held]
    hole, ambig = [], []
    for q, t, p, a in hw:
        content = [w for w in R.words(q) if w not in STOP]
        if content and all(freq[w] <= 1 for w in content):
            hole.append((q, t, p, a, content))
        else:
            ambig.append((q, t, p, a, content))
    print("\n%d of the %d held-out questions are STILL mis-routed with every\n"
          "other phrasing in the corpus seen.  That is the ceiling of this\n"
          "feature class on this corpus, and it splits:" % (len(hw), len(held)))
    print("\n  VOCABULARY HOLE (%d of %d) -- every content word occurs once in\n"
          "  the whole corpus, which is to say only here.  A CORPUS fix."
          % (len(hole), len(hw)))
    for q, t, p, a, c in hole:
        print("     %-22r %-9s -> %-9s  unseen %s" % (q, t, p, c))
    print("\n  AMBIGUOUS (%d of %d) -- the words are in the corpus and do not\n"
          "  carry this topic, or there are no content words at all."
          % (len(ambig), len(hw)))
    for q, t, p, a, c in ambig:
        print("     %-22r %-9s -> %-9s  content %-22s (%r)"
              % (q, t, p, c or "none", a))
    return 0


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--residual", action="store_true",
                    help="leave-one-out ceiling, and why what is left is left")
    a = ap.parse_args()
    if a.residual:
        C.check()
        return residual()
    C.check()
    tr = C.rows_of("train")
    # The router this replaced.  Taking the SHIPPED one apart is what
    # --residual does; this half of the file is the before picture and is
    # kept so the before/after in FINDINGS comes out of one program.
    W = R.fit_counts(tr, R.words)
    held = C.rows_of("dev") + C.rows_of("test")

    # vocabulary coverage
    trwords = set()
    for _t, q, _a, _h in tr:
        trwords |= set(R.words(q))
    print("train vocabulary: %d distinct words over %d questions"
          % (len(trwords), len(tr)))

    nfeat_seen = collections.Counter()
    for _t, q, _a, _h in held:
        ws = R.words(q)
        nfeat_seen[sum(1 for w in ws if w in W)] += 1
    print("held-out questions by number of IN-TABLE words:")
    for k in sorted(nfeat_seen):
        print("   %d known word(s): %3d questions" % (k, nfeat_seen[k]))

    cats = collections.Counter()
    blame = collections.Counter()
    blame_ok = collections.Counter()
    oov_qs, conf_qs = [], []
    margins_ok, margins_bad = [], []
    for topic, q, ans, _h in held:
        ws = R.words(q)
        s = R.score(q, W, R.words)
        p = R.argmax_low(s)
        srt = sorted(s, reverse=True)
        margin = srt[0] - srt[1]
        ok = C.TOPICS[p] == topic
        known = [w for w in ws if w in W]
        if ok:
            margins_ok.append(margin)
        else:
            margins_bad.append(margin)
        if not known:
            cats["ok/no-known-word" if ok else "ERR/no-known-word"] += 1
            if not ok:
                oov_qs.append((q, topic, C.TOPICS[p], ws, margin))
        elif margin == 0:
            cats["ok/tie" if ok else "ERR/tie"] += 1
            if not ok:
                oov_qs.append((q, topic, C.TOPICS[p], ws, margin))
        else:
            cats["ok/decisive" if ok else "ERR/decisive"] += 1
            if not ok:
                # which single word contributed most to the winning topic
                # over the true topic
                ti = C.TOPICS.index(topic)
                worst, wname = 0, None
                for w in ws:
                    ww = W.get(w)
                    if not ww:
                        continue
                    d = ww[p] - ww[ti]
                    if d > worst:
                        worst, wname = d, w
                blame[wname] += 1
                conf_qs.append((q, topic, C.TOPICS[p], margin, wname, worst))
        if ok:
            for w in ws:
                if w in W:
                    blame_ok[w] += 0

    n = len(held)
    print("\nheld-out error taxonomy (%d questions)" % n)
    for k in sorted(cats):
        print("   %-20s %3d  %5.1f%%" % (k, cats[k], 100 * cats[k] / n))
    nerr = sum(v for k, v in cats.items() if k.startswith("ERR"))
    print("   %-20s %3d  %5.1f%%   <- the 31.4%%" % ("TOTAL ERROR", nerr,
                                                     100 * nerr / n))

    print("\nERRORS WITH NO DECISION TO MAKE (no known word, or a tie) -- "
          "these fall to TOPICS[0]=%r" % C.TOPICS[0])
    for q, t, p, ws, m in oov_qs:
        known = [w for w in ws if w in W]
        print("   %-22r %-9s -> %-9s  words %-28s known %s"
              % (q, t, p, ",".join(ws), ",".join(known) or "-"))

    print("\nERRORS THE SCORER DECIDED, and the word most responsible")
    for q, t, p, m, wname, d in sorted(conf_qs, key=lambda r: -r[5]):
        print("   %-22r %-9s -> %-9s  margin %3d  blame %-8r (+%d)"
              % (q, t, p, m, wname, d))

    print("\nword blamed for the most decided errors")
    for w, c in blame.most_common(10):
        ww = W.get(w, [0] * 5)
        print("   %-10r %2d errors   weights %s   in-train-topics %s"
              % (w, c, " ".join("%4d" % x for x in ww),
                 sum(1 for _t in C.TOPICS
                     if any(w in R.words(q) for tt, q, _a, _h in tr
                            if tt == _t))))

    print("\nmargin of correct vs wrong decisions")
    def stat(xs):
        xs = sorted(xs)
        return "n=%3d  min %4d  median %4d  max %4d" % (
            len(xs), xs[0], xs[len(xs) // 2], xs[-1]) if xs else "n=0"
    print("   correct  %s" % stat(margins_ok))
    print("   wrong    %s" % stat(margins_bad))
    return 0


if __name__ == "__main__":
    sys.exit(main())
