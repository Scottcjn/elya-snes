; int8 MAC on the GSU: acc += w[i] * x[i], signed 8x8 through the GSU
; multiplier.  Unrolled by two and software-pipelined: a GSU RAM load stalls
; the pipeline if its destination register is used by the next instruction, so
; the pointer increments and the second load sit between each load and its use.
; r0 = accumulator (the default Sreg/Dreg, so ADD needs no prefix)
lorom
arch superfx
org $009200
    iwt r5,#$0000
    from r5
    ramb                    ; RAMBR = 0 -> Game Pak RAM bank $70
    iwt r1,#$0100           ; W bytes
    iwt r2,#$0200           ; X bytes
    iwt r0,#$0000
    iwt r12,#64
    iwt r13,#inner
    cache
inner:
    to r5
    ldb (r1)
    inc r1
    to r6
    ldb (r2)
    inc r2
    to r8
    ldb (r1)
    inc r1
    to r9
    ldb (r2)
    inc r2
    from r5
    to r7
    mult r6
    add r7
    from r8
    to r7
    mult r9
    add r7
    loop
    nop
    iwt r1,#$0500
    from r0
    stw (r1)
    stop
    nop
