; baseline: GSU start/stop overhead with no MAC body at all
lorom
arch superfx
org $009000
    iwt r0,#$0000
    from r0
    ramb
    stop
    nop
