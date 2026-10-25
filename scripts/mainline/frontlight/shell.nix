{ pkgs ? import <nixpkgs> { } }:
pkgs.mkShell {
  nativeBuildInputs = with pkgs; [
    gcc pkgsCross.aarch64-multiplatform.buildPackages.gcc
    gnumake bc bison flex openssl perl python3 elfutils dtc kmod
  ];
  hardeningDisable = [ "all" ];
}
