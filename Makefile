# elya-snes ROM builds.  ca65/ld65 V2.18 for the 65816, asar 1.91 for the GSU.
# cc65 ships no SNES linker config, so rom/lorom32.cfg and rom/lorom32fx.cfg
# are ours.
#
# make            build every ROM
# make game       the game cartridge (three acts on top of the same engine)
# make gamecheck  drive the game to act 3 and check it from its own SRAM
# make measure    build, run under ares, and print both cycles-per-MAC tables
# make fx         the SuperFX arm (EMULATOR ONLY -- the Kaico cart has no GSU)

CA65   := ca65
LD65   := ld65
ASAR   := $(HOME)/bin/asar
CFG    := rom/lorom32.cfg
FXCFG  := rom/lorom32fx.cfg

GSUKERNELS := $(addprefix rom/gsu/,k_empty.bin k_int8.bin k_tern.bin k_nomul.bin)

all: out/boot.sfc out/bench.sfc out/benchfast.sfc out/kern.sfc out/kernfast.sfc \
     out/fx1.sfc out/fx2.sfc nn

rom/data.inc: tools/gendata.py
	python3 tools/gendata.py $@

# ---- 65816 ROMs on the plain LoROM config --------------------------------
out/%.o: rom/%.s rom/snes.inc rom/data.inc
	@mkdir -p out
	$(CA65) --cpu 65816 -I rom -o $@ -l out/$*.lst $<

out/fx1.o: rom/fx1.s rom/snes.inc rom/data.inc rom/gsu/hello.bin
	@mkdir -p out
	$(CA65) --cpu 65816 -I rom -o $@ -l out/fx1.lst rom/fx1.s

rom/gsu/hello.bin: rom/gsu/hello.asm
	@mkdir -p out
	@head -c 32768 /dev/zero > out/_blank.sfc && $(ASAR) --no-title-check $< out/_blank.sfc
	@dd if=out/_blank.sfc of=$@ bs=1 skip=0 count=128 status=none

out/kern.o: rom/kern.s rom/snes.inc rom/kern.inc
	@mkdir -p out
	$(CA65) --cpu 65816 -I rom -o $@ -l out/kern.lst rom/kern.s

out/kernfast.o: rom/kern.s rom/snes.inc rom/kern.inc
	@mkdir -p out
	$(CA65) --cpu 65816 -DFASTROM=1 -I rom -o $@ -l out/kernfast.lst rom/kern.s

rom/kern.inc: tools/genkern.py
	python3 tools/genkern.py $@

out/boot.sfc out/bench.sfc out/kern.sfc out/kernfast.sfc out/fx1.sfc: out/%.sfc: out/%.o $(CFG)
	$(LD65) -C $(CFG) -o $@ -m out/$*.map $<
	python3 tools/fixhdr.py $@

# ---- the same measurements at 3.58 MHz ------------------------------------
out/benchfast.o: rom/bench.s rom/snes.inc rom/data.inc
	@mkdir -p out
	$(CA65) --cpu 65816 -DFASTROM=1 -I rom -o $@ -l out/benchfast.lst rom/bench.s

out/benchfast.sfc: out/benchfast.o $(CFG)
	$(LD65) -C $(CFG) -o $@ -m out/benchfast.map $<
	python3 tools/fixhdr.py $@

# ---- SuperFX ---------------------------------------------------------------
# GSU kernels carry absolute addresses (the LOOP target in R13), so each one is
# assembled at, and linked to, a fixed address.  Getting this wrong is silent.
rom/gsu/k_empty.bin: rom/gsu/k_empty.asm
	@head -c 32768 /dev/zero > out/_blank.sfc && $(ASAR) --no-title-check $< out/_blank.sfc
	@dd if=out/_blank.sfc of=$@ bs=1 skip=4096 count=128 status=none
rom/gsu/k_int8.bin: rom/gsu/k_int8.asm
	@head -c 32768 /dev/zero > out/_blank.sfc && $(ASAR) --no-title-check $< out/_blank.sfc
	@dd if=out/_blank.sfc of=$@ bs=1 skip=4608 count=128 status=none
rom/gsu/k_tern.bin: rom/gsu/k_tern.asm
	@head -c 32768 /dev/zero > out/_blank.sfc && $(ASAR) --no-title-check $< out/_blank.sfc
	@dd if=out/_blank.sfc of=$@ bs=1 skip=5120 count=128 status=none
rom/gsu/k_nomul.bin: rom/gsu/k_nomul.asm
	@head -c 32768 /dev/zero > out/_blank.sfc && $(ASAR) --no-title-check $< out/_blank.sfc
	@dd if=out/_blank.sfc of=$@ bs=1 skip=5632 count=128 status=none

out/fx2.o: rom/fx2.s rom/snes.inc rom/data.inc $(GSUKERNELS)
	@mkdir -p out
	$(CA65) --cpu 65816 -I rom -o $@ -l out/fx2.lst rom/fx2.s

out/fx2.sfc: out/fx2.o $(FXCFG)
	$(LD65) -C $(FXCFG) -o $@ -m out/fx2.map $<
	python3 tools/fixhdr.py $@

fx: out/fx2.sfc
	bash tools/run_ares.sh out/fx2.sfc >/dev/null
	cp $(HOME)/snesroms/fx2.ram out/fx2.ram
	python3 tools/analyze_fx.py out/fx2.ram | tee out/fx2_report.txt

# ---- run and analyse -------------------------------------------------------
# ---- the transformer cartridge --------------------------------------------
# Two-pass build (see build_nn.sh): the weight program calls the row handlers,
# so tools/emit.py needs addresses only ld65 knows.
nn:
	./build_nn.sh
	SNES_FAST=1 NAME=nnfast DEFS=-DFASTROM=1 ./build_nn.sh

# The game cartridge: the same engine with a presentation layer on top.
# rom/game.inc is pulled into rom/nn.s by -DGAME, so there is one engine and
# one set of weights, not two.
game:
	python3 tools/mkfont.py assets/font
	python3 tools/mkart.py assets/obj
	python3 tools/mkbg.py assets
	python3 tools/mkgame.py out/game
	SNES_FAST=0 NAME=game     DEFS="-DGAME"             ./build_nn.sh
	SNES_FAST=1 NAME=gamefast DEFS="-DGAME -DFASTROM=1" ./build_nn.sh
	python3 tools/kaico_check.py out/game.sfc out/gamefast.sfc

# The game's own gate: a scripted player drives it to act 3, the ROM dumps its
# OAM/VRAM/CGRAM into SRAM, and the host checks the coin binding, re-runs every
# generated line through host/ref.py and rebuilds the frames.
gamecheck:
	SNES_FAST=0 NAME=gameqa  DEFS="-DGAME -DGAUTO -DGRUN=2" ./build_nn.sh
	SNES_FAST=0 NAME=gamectl DEFS="-DGAME -DGAUTO -DNOGEN -DGFRAMES=1700" ./build_nn.sh
	bash tools/run_ares.sh out/gameqa.sfc  > /dev/null
	bash tools/run_ares.sh out/gamectl.sfc > /dev/null
	cp $(HOME)/snesroms/gameqa.ram $(HOME)/snesroms/gamectl.ram out/
	python3 tools/check_game.py out/gameqa.ram out/gamectl.ram
	python3 tools/render_frame.py out/gameqa.ram out/frames

# Build every variant, run every one under ares, check every token against
# host/ref.py.  This is the shipping gate.
gate:
	./gate.sh

# the gather-kernel A/B of FINDINGS entry 6
kernels: out/kern.sfc out/kernfast.sfc
	bash tools/run_ares.sh out/kern.sfc >/dev/null
	cp $(HOME)/snesroms/kern.ram out/kern.ram
	python3 tools/analyze_kern.py out/kern.ram | tee out/kern_report.txt
	bash tools/run_ares.sh out/kernfast.sfc >/dev/null
	cp $(HOME)/snesroms/kernfast.ram out/kernfast.ram
	python3 tools/analyze_kern.py out/kernfast.ram --fast | tee out/kernfast_report.txt

measure: out/bench.sfc out/benchfast.sfc
	bash tools/run_ares.sh out/bench.sfc >/dev/null
	cp $(HOME)/snesroms/bench.ram out/bench.ram
	python3 tools/analyze.py out/bench.ram | tee out/bench_report.txt
	bash tools/run_ares.sh out/benchfast.sfc >/dev/null
	cp $(HOME)/snesroms/benchfast.ram out/benchfast.ram
	python3 tools/analyze.py out/benchfast.ram --fast | tee out/benchfast_report.txt

clean:
	rm -f out/*.o out/*.sfc out/*.map out/*.lst out/_blank.sfc

.PHONY: all clean measure fx nn game gamecheck gate kernels
.PRECIOUS: out/%.o rom/gsu/%.bin
