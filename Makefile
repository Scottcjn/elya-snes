# elya-snes ROM builds.  ca65 V2.18 / ld65, LoROM 32 KiB, our own linker cfg.
#
# ares (flatpak) cannot see /tmp, so ROMs are staged under $HOME.

CA65   := ca65
LD65   := ld65
CFG    := rom/lorom32.cfg
STAGE  := $(HOME)/snesroms

ROMS   := boot bench

all: $(addprefix out/,$(addsuffix .sfc,$(ROMS)))

out/%.o: rom/%.s rom/snes.inc rom/data.inc
	@mkdir -p out
	$(CA65) --cpu 65816 -I rom -o $@ -l out/$*.lst $<

out/_unused_%.o: rom/%.s
	@mkdir -p out
	$(CA65) --cpu 65816 -o $@ -l out/$*.lst $<

out/%.sfc: out/%.o $(CFG)
	$(LD65) -C $(CFG) -o $@ -m out/$*.map $<
	python3 tools/fixhdr.py $@

# copy into a flatpak-visible directory
stage: all
	@mkdir -p $(STAGE)
	cp out/*.sfc $(STAGE)/
	@ls -l $(STAGE)

clean:
	rm -rf out/*.o out/*.sfc out/*.map out/*.lst

.PHONY: all stage clean
.PRECIOUS: out/%.o

# same measurements at 3.58 MHz
out/benchfast.o: rom/bench.s rom/snes.inc rom/data.inc
	@mkdir -p out
	$(CA65) --cpu 65816 -DFASTROM=1 -I rom -o $@ -l out/benchfast.lst rom/bench.s

out/benchfast.sfc: out/benchfast.o $(CFG)
	$(LD65) -C $(CFG) -o $@ -m out/benchfast.map $<
	python3 tools/fixhdr.py $@

fast: out/benchfast.sfc
.PHONY: fast
