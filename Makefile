# elya-snes ROM builds.  ca65/ld65 V2.18 for the 65816, asar 1.91 for the GSU.
# cc65 ships no SNES linker config, so rom/lorom32.cfg and rom/lorom32fx.cfg
# are ours.
#
# make            build every ROM
# make measure    build, run under ares, and print both cycles-per-MAC tables
# make fx         the SuperFX arm (EMULATOR ONLY -- the Kaico cart has no GSU)

CA65   := ca65
LD65   := ld65
ASAR   := $(HOME)/bin/asar
CFG    := rom/lorom32.cfg
FXCFG  := rom/lorom32fx.cfg

GSUKERNELS := $(addprefix rom/gsu/,k_empty.bin k_int8.bin k_tern.bin k_nomul.bin)

all: out/boot.sfc out/bench.sfc out/benchfast.sfc out/fx1.sfc out/fx2.sfc

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

out/boot.sfc out/bench.sfc out/fx1.sfc: out/%.sfc: out/%.o $(CFG)
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
measure: out/bench.sfc out/benchfast.sfc
	bash tools/run_ares.sh out/bench.sfc >/dev/null
	cp $(HOME)/snesroms/bench.ram out/bench.ram
	python3 tools/analyze.py out/bench.ram | tee out/bench_report.txt
	bash tools/run_ares.sh out/benchfast.sfc >/dev/null
	cp $(HOME)/snesroms/benchfast.ram out/benchfast.ram
	python3 tools/analyze.py out/benchfast.ram --fast | tee out/benchfast_report.txt

clean:
	rm -f out/*.o out/*.sfc out/*.map out/*.lst out/_blank.sfc

.PHONY: all clean measure fx
.PRECIOUS: out/%.o rom/gsu/%.bin
