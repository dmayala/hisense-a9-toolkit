# Toolchain for scripts/mainline/kernel/build.sh: arm64 cross gcc + kernel build deps.
# Plain nixpkgs channel; not an AOSP build (CLAUDE.md), so NixOS is fine here.
{ pkgs ? import <nixpkgs> { } }:
pkgs.mkShell {
  nativeBuildInputs = with pkgs; [
    pkgsCross.aarch64-multiplatform.buildPackages.gcc
    gnumake bc bison flex openssl perl python3 elfutils dtc cpio kmod gzip lz4 ncurses pkg-config
  ];
  # nixpkgs' hardening wrappers break kernel builds of host tools
  hardeningDisable = [ "all" ];
}
