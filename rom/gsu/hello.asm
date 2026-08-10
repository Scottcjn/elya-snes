lorom
arch superfx
org $008000
    iwt r0,#$0000
    from r0
    ramb                ; RAMBR = 0  -> Game Pak RAM bank $70
    iwt r1,#$0100
    iwt r0,#$BEEF
    from r0
    stw (r1)
    nop
    nop
    iwt r0,#$1111
    stop
    nop
