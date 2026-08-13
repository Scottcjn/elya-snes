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
"""
import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import corpus as C
import router as R


def main():
    C.check()
    tr = C.rows_of("train")
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
