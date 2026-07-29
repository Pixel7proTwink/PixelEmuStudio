# Сборка pixemu-core в среде MSYS2 (UCRT64):
#   pacman -S mingw-w64-ucrt-x86_64-gcc make
#   cd native && make
CXX      ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -municode -static-libgcc -static-libstdc++
LDLIBS    = -lwinhttp -lbcrypt

all: bin/pixemu-core.exe

bin/pixemu-core.exe: src/main.cpp | bin
	$(CXX) $(CXXFLAGS) $< -o $@ $(LDLIBS)
	@echo OK: $@

bin:
	mkdir -p bin

clean:
	rm -rf bin

.PHONY: all clean
