; ternary sign-separated gather on the GSU: no multiply anywhere, but one extra
; memory read per accumulate for the index, plus an address add.  Unrolled by
; two so half the accumulates are subtractions, and interleaved so neither load
; is used by the instruction that follows it.
lorom
arch superfx
org $009400
    iwt r5,#$0000
    from r5
    ramb
    iwt r1,#$0300           ; IDX, pre-doubled word indices
    iwt r2,#$0400           ; XS16 base, sign-extended activations
    iwt r0,#$0000
    iwt r12,#64
    iwt r13,#inner
    cache
inner:
    to r5
    ldw (r1)
    inc r1
    inc r1
    to r8
    ldw (r1)
    inc r1
    inc r1
    from r5
    to r6
    add r2
    from r8
    to r9
    add r2
    to r7
    ldw (r6)
    to r10
    ldw (r9)
    add r7
    sub r10
    loop
    nop
    iwt r1,#$0500
    from r0
    stw (r1)
    stop
    nop
